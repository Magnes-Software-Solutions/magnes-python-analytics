import datetime
import io
import json
import logging
import os
import random

import boto3
import mysql.connector
import pandas as pd
from collections import defaultdict
from sklearn.linear_model import LinearRegression

# -------------------------------------------------------------------
# Configurações de ambiente e logging
# -------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

BUCKET = os.environ.get("BUCKET", "s3-projeto-magnes-2026.04.09")
RAW_KEY = os.environ.get("RAW_KEY", "raw/dadosBrutos.csv")
TRUSTED_KEY = os.environ.get("TRUSTED_KEY", "trusted/dadosTratados.csv")
CLIENT_KEY = os.environ.get("CLIENT_KEY", "client/dadosPerfeitos.json")

# -------------------------------------------------------------------
# Conexão com MySQL
# -------------------------------------------------------------------
def conectar_mysql():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
    )

# -------------------------------------------------------------------
# Funções auxiliares para classificação e regressão (Andrei)
# -------------------------------------------------------------------
def classificarStatusAtual(valorAtual, limite):
    if limite is None:
        return "Desconhecido"
    if valorAtual < limite * 0.8:
        return "Estável"
    if valorAtual < limite:
        return "Anormal"
    return "Crítico"

def classificarOscilacao(valorAtual, mediaHistorica, desvio):
    distancia = abs(valorAtual - mediaHistorica)
    if distancia <= desvio:
        return "Baixa (abaixo que 1σ)"
    if distancia <= desvio * 2:
        return "Média (entre 1σ e 2σ)"
    if distancia <= desvio * 3:
        return "Alta  (entre 2σ e 3σ)"
    return "Severa (acima de 3σ)"

def classificarDegradacao(mediaHistorica, limite):
    if limite is None:
        return "Desconhecido"
    distancia = limite - mediaHistorica
    if distancia >= 15:
        return "Recuperação"
    if distancia > 5:
        return "Degradação Média"
    return "Degradação Alta"

def regressaoLinear(ultimas2hMaquina, componente):
    """Retorna coeficientes da reta de regressão e previsão de atingir 100%."""
    if ultimas2hMaquina.empty or len(ultimas2hMaquina) < 2:
        return {"a": 0, "b": 0, "reta": [], "previsao100": "Sem previsão (poucos dados)"}

    df = ultimas2hMaquina.copy()
    df["horas"] = pd.to_datetime(df["horas"], errors="coerce")
    df[componente] = pd.to_numeric(df[componente], errors="coerce")
    df = df.dropna(subset=["horas", componente]).sort_values("horas")

    if df.empty or len(df) < 2:
        return {"a": 0, "b": 0, "reta": [], "previsao100": "Sem dados suficientes"}

    df["x"] = (df["horas"] - df["horas"].min()).dt.total_seconds()
    X = df[["x"]]
    y = df[componente]

    modelo = LinearRegression()
    modelo.fit(X, y)

    a = float(modelo.coef_[0])
    b = float(modelo.intercept_)

    previsao100 = "Sem previsão"
    if abs(a) > 0.0001 and a > 0:
        x100 = (100 - b) / a
        if 0 <= x100 <= 315360000:  # até 10 anos
            data_previsao = pd.to_datetime(df["horas"].min()) + pd.to_timedelta(x100, unit="s")
            previsao100 = "≈" + str(data_previsao)

    yMin = a * df["x"].min() + b
    yMax = a * df["x"].max() + b
    reta = [
        {"x": str(df["horas"].min()), "y": round(yMin, 2)},
        {"x": str(df["horas"].max()), "y": round(yMax, 2)},
    ]

    return {"a": a, "b": round(b, 2), "reta": reta, "previsao100": previsao100}

def penalidadeSaudeComponente(statusAtual, oscilacao, degradacao, tendenciaA):
    penalidade = 0
    if statusAtual == "Anormal":
        penalidade += 7.5
    elif statusAtual == "Crítico":
        penalidade += 11.2
    if oscilacao == "Média (entre 1σ e 2σ)":
        penalidade += 3.7
    elif oscilacao == "Alta  (entre 2σ e 3σ)":
        penalidade += 5.2
    elif oscilacao == "Severa (acima de 3σ)":
        penalidade += 7.5
    if degradacao == "Degradação Média":
        penalidade += 3.7
    elif degradacao == "Degradação Alta":
        penalidade += 7.5
    if tendenciaA > 0.005:
        penalidade += 3.7
    elif tendenciaA > 0.002:
        penalidade += 1.5
    return penalidade

# -------------------------------------------------------------------
# Simulações determinísticas (Caio / Andrei)
# -------------------------------------------------------------------
def _simular_imagem(mac_address):
    TIPOS_DICOM = ["T1-weighted", "T2-weighted", "FLAIR", "DWI", "T1 contrast", "BOLD fMRI"]
    seed = int(mac_address.replace(":", ""), 16) % (2**32)
    rng = random.Random(seed)
    return {
        "tamanhoGB": round(rng.uniform(1.5, 8.0), 2),
        "tipoDicom": rng.choice(TIPOS_DICOM),
    }

def _simular_corrente(mac_address):
    """Gera valor de corrente elétrica (mA) determinístico por MAC."""
    seed = int(mac_address.replace(":", ""), 16) % (2**32)
    rng = random.Random(seed)
    if rng.random() <= 0.6:
        return rng.randint(115, 135)
    return rng.choice([rng.randint(90, 114), rng.randint(136, 160)])

def _simular_poeira(mac_address):
    """Gera nível de poeira (0-100%) determinístico por MAC."""
    seed = int(mac_address.replace(":", ""), 16) % (2**32)
    rng = random.Random(seed)
    return rng.randint(0, 100)

# -------------------------------------------------------------------
# Agrupamento e KPIs agregadas (Caio)
# -------------------------------------------------------------------
def _agrupar_por_mac(linhas_client):
    grupos = defaultdict(list)
    for linha in linhas_client:
        grupos[linha["macAddress"]].append(linha)
    for mac in grupos:
        grupos[mac].sort(key=lambda x: x["horario"])
    return grupos

def gerar_kpis(linhas_client):
    grupos = _agrupar_por_mac(linhas_client)

    # KPI 1 – máquina mais crítica (maior ramUso no último registro)
    ultimos = {mac: registros[-1] for mac, registros in grupos.items() if registros}
    kpi1_mac = max(ultimos, key=lambda mac: ultimos[mac]["ramUso"])
    machine_mais_critica = {
        "macAddress": kpi1_mac,
        "empresa": ultimos[kpi1_mac]["empresa"],
        "ramUso": ultimos[kpi1_mac]["ramUso"],
        "ramUsoBruto": ultimos[kpi1_mac]["ramUsoBruto"],
    }

    # KPI 2 – maior variação de RAM entre os dois últimos registros
    variacoes = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        variacao = abs(registros[-1]["ramUso"] - registros[-2]["ramUso"])
        variacoes[mac] = {
            "variacao": round(variacao, 2),
            "ultimo": registros[-1]["ramUso"],
            "penultimo": registros[-2]["ramUso"],
            "empresa": registros[-1]["empresa"],
        }
    if variacoes:
        kpi2_mac = max(variacoes, key=lambda mac: variacoes[mac]["variacao"])
        maior_variacao = {
            "macAddress": kpi2_mac,
            "empresa": variacoes[kpi2_mac]["empresa"],
            "variacao": variacoes[kpi2_mac]["variacao"],
            "ultimo": variacoes[kpi2_mac]["ultimo"],
            "penultimo": variacoes[kpi2_mac]["penultimo"],
        }
    else:
        maior_variacao = None

    # KPI 3 – pior tendência (maior delta positivo de RAM)
    tendencias = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        delta = registros[-1]["ramUso"] - registros[-2]["ramUso"]
        tendencias[mac] = {
            "delta": round(delta, 2),
            "ramUso": registros[-1]["ramUso"],
            "empresa": registros[-1]["empresa"],
        }
    if tendencias:
        kpi3_mac = max(tendencias, key=lambda mac: tendencias[mac]["delta"])
        pior_tendencia = {
            "macAddress": kpi3_mac,
            "empresa": tendencias[kpi3_mac]["empresa"],
            "delta": tendencias[kpi3_mac]["delta"],
            "ramUso": tendencias[kpi3_mac]["ramUso"],
        }
    else:
        pior_tendencia = None

    # KPI 4 – imagem mais pesada (crescimento de disco ou fallback simulado)
    crescimentos = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        delta_disco = registros[-1]["discoUsoBruto"] - registros[-2]["discoUsoBruto"]
        if delta_disco > 0:
            crescimentos[mac] = {
                "tamanhoGB": round(delta_disco, 2),
                "empresa": registros[-1]["empresa"],
                "simulado": False,
            }
    if crescimentos:
        kpi4_mac = max(crescimentos, key=lambda mac: crescimentos[mac]["tamanhoGB"])
        imagem_pesada = {
            "macAddress": kpi4_mac,
            "empresa": crescimentos[kpi4_mac]["empresa"],
            "tamanhoGB": crescimentos[kpi4_mac]["tamanhoGB"],
            "tipoDicom": _simular_imagem(kpi4_mac)["tipoDicom"],
            "simulado": False,
        }
    else:
        logger.warning("KPI 4: sem crescimento de disco detectado. Usando simulação.")
        kpi4_mac = max(ultimos, key=lambda mac: _simular_imagem(mac)["tamanhoGB"])
        simulacao = _simular_imagem(kpi4_mac)
        imagem_pesada = {
            "macAddress": kpi4_mac,
            "empresa": ultimos[kpi4_mac]["empresa"],
            "tamanhoGB": simulacao["tamanhoGB"],
            "tipoDicom": simulacao["tipoDicom"],
            "simulado": True,
        }

    return {
        "geradoEm": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "machineMaisCritica": machine_mais_critica,
        "maiorVariacaoRam": maior_variacao,
        "piorTendencia": pior_tendencia,
        "imagemMaisPesada": imagem_pesada,
    }

def gerar_ranking(linhas_client):
    grupos = _agrupar_por_mac(linhas_client)
    ranking = []
    for mac, registros in grupos.items():
        ultimo = registros[-1]
        ranking.append({
            "macAddress": mac,
            "empresa": ultimo["empresa"],
            "ramUso": ultimo["ramUso"],
            "ramUsoBruto": ultimo["ramUsoBruto"],
            "horario": ultimo["horario"],
        })
    ranking.sort(key=lambda x: x["ramUso"], reverse=True)
    for i, item in enumerate(ranking, start=1):
        item["posicao"] = i
    return ranking

def gerar_historico(linhas_client, limite=1):
    grupos = _agrupar_por_mac(linhas_client)
    historico = []
    for mac, registros in grupos.items():
        ultimos = registros[-limite:]
        historico.append({
            "macAddress": mac,
            "empresa": registros[-1]["empresa"],
            "registros": [
                {
                    "horario": r["horario"],
                    "ramUso": r["ramUso"],
                    "ramUsoBruto": r["ramUsoBruto"],
                }
                for r in ultimos
            ],
        })
    return historico

# -------------------------------------------------------------------
# Transformação de linha bruta para trusted (original)
# -------------------------------------------------------------------
def gerar_linha_trusted(cursor, linha):
    horas = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mac_address = linha["macAddress"]
    empresa = buscar_empresa(cursor, mac_address)

    ram_livre = round(linha["ramLivre"] / 1024**3, 2)
    ram_usada = round(linha["ramUsada"] / 1024**3, 2)
    ram_total = round(linha["ramTotal"] / 1024**3, 2)

    disco_livre = round(linha["discoLivre"] / 1024**3, 2)
    disco_usado = round(linha["discoUsado"] / 1024**3, 2)
    disco_total = round(linha["discoTotal"] / 1024**3, 2)

    porcentagem_ram = round((ram_usada / ram_total) * 100, 2) if ram_total else 0
    porcentagem_disco = round((disco_usado / disco_total) * 100, 2) if disco_total else 0

    return {
        "empresa": empresa,
        "macAddress": mac_address,
        "horas": horas,
        "cpuPorcentagem": linha["cpuPorcentagem"],
        "cpuNucleosFisicos": linha["cpuNucleosFisicos"],
        "cpuNucleosLogicos": linha["cpuNucleosLogicos"],
        "cpuTempoUser": round(linha["cpuTempoUser"] / 60),
        "cpuTempoSistema": linha["cpuTempoSistema"],
        "cpuTempoInativo": linha["cpuTempoInativo"],
        "ramLivre": ram_livre,
        "ramUsada": ram_usada,
        "ramTotal": ram_total,
        "discoLivre": disco_livre,
        "discoUsado": disco_usado,
        "discoTotal": disco_total,
        "porcentagemRam": porcentagem_ram,
        "porcentagemDisco": porcentagem_disco,
        "totalProcessos": linha["totalProcessos"],
    }

def buscar_empresa(cursor, mac_address):
    cursor.execute(
        """
        SELECT r.razaoSocial
        FROM maquina m
        JOIN redeHospital r ON m.fkRedeHospital = r.idRedeHospital
        WHERE m.macAddress = %s
        """,
        (mac_address,),
    )
    resultado = cursor.fetchone()
    return resultado[0] if resultado else None

# -------------------------------------------------------------------
# Consultas em lote (limites e dados financeiros)
# -------------------------------------------------------------------
def buscar_limites_em_lote(cursor, lista_macs):
    if not lista_macs:
        return {}
    format_strings = ",".join(["%s"] * len(lista_macs))
    query = f"""
        SELECT m.macAddress,
               MAX(CASE WHEN c.tipoComponente = 'Processador' THEN cm.limite END) as limiteCPU,
               MAX(CASE WHEN c.tipoComponente = 'Memória' THEN cm.limite END) as limiteRAM,
               MAX(CASE WHEN c.tipoComponente = 'Armazenamento' THEN cm.limite END) as limiteDisco
        FROM componente_maquina cm
        JOIN componente c ON cm.fkComponente = c.idComponente
        JOIN maquina m ON cm.fkMaquina = m.idMaquina
        WHERE m.macAddress IN ({format_strings})
        GROUP BY m.macAddress
    """
    cursor.execute(query, lista_macs)
    resultados = cursor.fetchall()
    limites = {}
    for mac, cpu, ram, disco in resultados:
        limites[mac] = {"cpu": cpu, "ram": ram, "disco": disco}
    return limites

def buscar_dados_financeiros_em_lote(cursor, lista_macs):
    if not lista_macs:
        return {}
    format_strings = ",".join(["%s"] * len(lista_macs))
    query = f"""
        SELECT m.macAddress,
               m.valorMedioExame,
               m.examesPorHora,
               m.metaSLA,
               m.custoCorretiva,
               e.bairro,
               e.cidade,
               e.numeroEstabelecimento,
               e.cep
        FROM maquina m
        JOIN enderecoHospital e ON m.fkEnderecoHospital = e.idEnderecoHospital
        WHERE m.macAddress IN ({format_strings})
    """
    cursor.execute(query, lista_macs)
    resultados = cursor.fetchall()
    dados = {}
    for row in resultados:
        mac, valor, exames, meta, custo, bairro, cidade, num, cep = row
        dados[mac] = {
            "valorExame": float(valor) if valor is not None else 0.0,
            "examesPorHora": exames if exames is not None else 0,
            "metaSLA": float(meta) if meta is not None else 100.0,
            "custoCorretiva": float(custo) if custo is not None else 0.0,
            "bairro": bairro,
            "cidade": cidade,
            "numero": num,
            "cep": cep,
        }
    return dados

# -------------------------------------------------------------------
# Construção do registro enriquecido por máquina (saúde + financeiro)
# -------------------------------------------------------------------
def construir_registro_cliente(trusted_row, limites_mac, financeiro_mac, historico_2h_mac):
    mac = trusted_row["macAddress"]
    cpu_uso = float(trusted_row["cpuPorcentagem"])
    ram_uso = float(trusted_row["porcentagemRam"])
    ram_bruto = float(trusted_row["ramUsada"])
    disco_bruto = float(trusted_row["discoUsado"])

    limite_cpu = limites_mac.get("cpu")
    limite_ram = limites_mac.get("ram")

    # Alertas
    alerta_cpu = limite_cpu is not None and cpu_uso > limite_cpu
    alerta_ram = limite_ram is not None and ram_uso > limite_ram

    # Métricas históricas para saúde (últimas 2h)
    media_cpu_2h = round(historico_2h_mac["cpuPorcentagem"].mean(), 2)
    media_ram_2h = round(historico_2h_mac["porcentagemRam"].mean(), 2)
    desvio_cpu = historico_2h_mac["cpuPorcentagem"].std()
    desvio_cpu = round(desvio_cpu, 2) if pd.notna(desvio_cpu) else 0
    desvio_ram = historico_2h_mac["porcentagemRam"].std()
    desvio_ram = round(desvio_ram, 2) if pd.notna(desvio_ram) else 0

    # Classificações
    status_cpu = classificarStatusAtual(cpu_uso, limite_cpu)
    status_ram = classificarStatusAtual(ram_uso, limite_ram)
    oscilacao_cpu = classificarOscilacao(cpu_uso, media_cpu_2h, desvio_cpu)
    oscilacao_ram = classificarOscilacao(ram_uso, media_ram_2h, desvio_ram)
    degradacao_cpu = classificarDegradacao(media_cpu_2h, limite_cpu)
    degradacao_ram = classificarDegradacao(media_ram_2h, limite_ram)

    # Regressão linear
    previsao_cpu = regressaoLinear(historico_2h_mac, "cpuPorcentagem")
    previsao_ram = regressaoLinear(historico_2h_mac, "porcentagemRam")

    # Índice de saúde
    penalidade_cpu = penalidadeSaudeComponente(status_cpu, oscilacao_cpu, degradacao_cpu, previsao_cpu["a"])
    penalidade_ram = penalidadeSaudeComponente(status_ram, oscilacao_ram, degradacao_ram, previsao_ram["a"])
    saude = 100 - (penalidade_cpu + penalidade_ram)
    saude_str = f"{saude:.2f} / 100"

    # KPIs financeiras (Anna)
    minutos_downtime = 45 if cpu_uso < 2 else 0
    horas_offline = minutos_downtime / 60
    fin = financeiro_mac
    perda_indisponibilidade = horas_offline * fin["valorExame"] * fin["examesPorHora"]

    if alerta_cpu or alerta_ram:
        if fin["custoCorretiva"] > 0:
            lucro_retido = max(fin["custoCorretiva"] - 450.0, 0.0)
        else:
            lucro_retido = 0.0
        perda_lentidao = 0.25 * fin["valorExame"] * fin["examesPorHora"]
        perda_indisponibilidade += perda_lentidao
    else:
        lucro_retido = 0.0

    uptime_real = 100 - ((minutos_downtime / 43200) * 100)
    if alerta_cpu:
        uptime_real -= 2.5

    status_sla = "CONFORME" if uptime_real >= fin["metaSLA"] else "VIOLADO"

    # Ambiental simulado
    corrente = _simular_corrente(mac)
    poeira = _simular_poeira(mac)

    # Registro final
    return {
        "empresa": trusted_row["empresa"],
        "macAddress": mac,
        "horario": str(trusted_row["horas"]),
        "cpu": {
            "uso": cpu_uso,
            "limite": limite_cpu,
            "status": status_cpu,
            "oscilacao": oscilacao_cpu,
            "degradacao": degradacao_cpu,
            "previsao": previsao_cpu,
        },
        "ram": {
            "uso": ram_uso,
            "limite": limite_ram,
            "status": status_ram,
            "oscilacao": oscilacao_ram,
            "degradacao": degradacao_ram,
            "previsao": previsao_ram,
        },
        "indiceSaude": saude_str,
        "financeiro": {
            "localizacao": {
                "cidade": fin["cidade"],
                "bairro": fin["bairro"],
                "numero": fin["numero"],
                "cep": fin["cep"],
            },
            "alertaCPU": alerta_cpu,
            "alertaRAM": alerta_ram,
            "kpiPerdaIndisponibilidade": round(perda_indisponibilidade, 2),
            "custoCorretivaPotencial": fin["custoCorretiva"],
            "economiaPreditiva": 450.0,
            "kpiLucroRetido": round(lucro_retido, 2),
            "kpiConformidadeSLA": round(max(uptime_real, 0.0), 2),
            "metaSLA": fin["metaSLA"],
            "statusSLA": status_sla,
            "confiabilidadeAtivo": round(100 - cpu_uso, 2),
        },
        "ambiente": {
            "corrente": corrente,
            "poeira": poeira,
        },
        # Campos necessários para Caio (KPIs agregadas)
        "ramUso": ram_uso,
        "ramUsoBruto": ram_bruto,
        "discoUsoBruto": disco_bruto,
    }

# -------------------------------------------------------------------
# Lambda principal
# -------------------------------------------------------------------
def lambda_handler(event, context):
    logger.info("Iniciando ETL unificado")

    # 1. Leitura do raw
    response = s3.get_object(Bucket=BUCKET, Key=RAW_KEY)
    df_raw = pd.read_csv(response["Body"])
    if df_raw.empty:
        logger.info("Arquivo raw vazio")
        return {"statusCode": 200, "body": "Arquivo raw vazio"}

    conn = conectar_mysql()
    cursor = conn.cursor()

    try:
        # 2. Carrega trusted existente (se houver)
        try:
            resp = s3.get_object(Bucket=BUCKET, Key=TRUSTED_KEY)
            df_trusted = pd.read_csv(resp["Body"])
            logger.info("Trusted carregado com %d registros", len(df_trusted))
        except Exception:
            logger.info("Nenhum trusted anterior encontrado, iniciando novo")
            df_trusted = pd.DataFrame()

        # 3. Gera novas linhas trusted e as concatena ao histórico
        novas_trusted = []
        for _, linha in df_raw.iterrows():
            nova = gerar_linha_trusted(cursor, linha)
            novas_trusted.append(nova)

        df_novas = pd.DataFrame(novas_trusted)
        df_trusted = pd.concat([df_trusted, df_novas], ignore_index=True)

        # 4. Persiste trusted atualizado no S3
        trusted_buffer = io.StringIO()
        df_trusted.to_csv(trusted_buffer, index=False)
        s3.put_object(
            Bucket=BUCKET,
            Key=TRUSTED_KEY,
            Body=trusted_buffer.getvalue().encode("utf-8"),
            ContentType="text/csv",
        )

        # 5. Coleta MACs das novas linhas para consultas em lote
        macs_novos = df_novas["macAddress"].unique().tolist()

        limites = buscar_limites_em_lote(cursor, macs_novos)
        dados_financeiros = buscar_dados_financeiros_em_lote(cursor, macs_novos)

        # 6. Prepara histórico 2h para cada máquina (sobre todo o trusted)
        agora = pd.Timestamp.now()
        limite_2h = agora - pd.Timedelta(hours=2)
        ultimas_2h = df_trusted[pd.to_datetime(df_trusted["horas"]) >= limite_2h]

        # 7. Constrói registros cliente enriquecidos
        client_records = []
        for _, trusted_row in df_novas.iterrows():
            mac = trusted_row["macAddress"]
            hist_mac = ultimas_2h[ultimas_2h["macAddress"] == mac]
            limite_mac = limites.get(mac, {})
            financeiro_mac = dados_financeiros.get(mac, {
                "valorExame": 0.0, "examesPorHora": 0, "metaSLA": 100.0,
                "custoCorretiva": 0.0, "bairro": "N/A", "cidade": "N/A",
                "numero": "N/A", "cep": "N/A",
            })
            record = construir_registro_cliente(trusted_row, limite_mac, financeiro_mac, hist_mac)
            client_records.append(record)

        # 8. Gera KPIs agregadas, ranking e histórico (Caio)
        kpis = gerar_kpis(client_records)
        ranking = gerar_ranking(client_records)
        historico = gerar_historico(client_records)

        # 9. Monta objeto final único
        output = {
            "maquinas": client_records,
            "kpis": kpis,
            "ranking": ranking,
            "historico": historico,
        }

        # 10. Salva JSON final no S3
        client_json = json.dumps(output, ensure_ascii=False, indent=2)
        s3.put_object(
            Bucket=BUCKET,
            Key=CLIENT_KEY,
            Body=client_json.encode("utf-8"),
            ContentType="application/json",
        )

        logger.info("ETL finalizada. Linhas processadas: %d", len(df_raw))
        return {
            "statusCode": 200,
            "body": json.dumps({
                "mensagem": "ETL finalizada com sucesso",
                "linhasProcessadas": len(df_raw),
                "client": CLIENT_KEY,
            }, ensure_ascii=False),
        }

    finally:
        cursor.close()
        conn.close()

        