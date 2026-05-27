import datetime
import io
import json
import logging
import os
import random

import boto3
import mysql.connector
import numpy as np
import pandas as pd
from collections import defaultdict

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

BUCKET = os.environ.get("BUCKET", "magnes-solutions")
RAW_KEY = os.environ.get("RAW_KEY", "raw/dadosBrutos.csv")
TRUSTED_KEY = os.environ.get("TRUSTED_KEY", "trusted/dadosTratados.csv")
CLIENT_KEY = os.environ.get("CLIENT_KEY", "client/dadosPerfeitos.json")

def conectar_mysql():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
    )

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

    df["x"] = (df["horas"] - df["horas"].min()).dt.total_seconds() / 600
    x = df["x"].values
    y = df[componente].values

    mascara_valida = (np.isfinite(x) & np.isfinite(y))
    x = x[mascara_valida]
    y = y[mascara_valida]

    if len(x) < 2:
        return {"a": 0, "b": 0, "reta": [], "previsao100": "Sem dados válidos"}
    
    if np.all(x == x[0]):
        return {"a": 0, "b": round(float(np.mean(y)), 2), "reta": [], "previsao100": "Sem variação temporal"}

    try:
        a, b = np.polyfit(x, y, 1)
        a, b = float(a), float(b)

        yPrevisto = a * x + b
        ss_res = np.sum((y - yPrevisto) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)

        if ss_tot == 0:
            r2 = 0
        else:
            r2 = 1 - (ss_res / ss_tot)

        r2 = float(r2)

    except Exception as erro:
        logger.warning("Erro regressao linear: %s", erro)

        return {
            "a": 0,
            "b": 0,
            "reta": [],
            "previsao100": "Erro regressão"
        }

    previsao100 = "Sem previsão"

    if a > 0.05 and r2 >= 0.7:
        x100 = (100 - b) / a
        if 0 <= x100 <= 8640:  # até 2 meses
            data_previsao = pd.to_datetime(df["horas"].min()) + pd.to_timedelta(x100 * 10, unit="m")
            previsao100 = "≈" + str(data_previsao)

    yMin = a * x.min() + b
    yMax = a * x.max() + b
    reta = [
        {"x": str(df["horas"].min()), "y": round(yMin, 2)},
        {"x": str(df["horas"].max()), "y": round(yMax, 2)},
    ]

    return {"a": a, "b": round(b, 2), "reta": reta, "previsao100": previsao100}

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

def _simular_imagem(mac_address):
    TIPOS_DICOM = ["T1-weighted", "T2-weighted", "FLAIR", "DWI", "T1 contrast", "BOLD fMRI"]
    seed = int(mac_address.replace(":", ""), 16) % (2**32)
    rng = random.Random(seed)
    return {
        "tamanhoGB": round(rng.uniform(1.5, 8.0), 2),
        "tipoDicom": rng.choice(TIPOS_DICOM),
    }

def _simular_corrente(mac_address):
    seed = int(mac_address.replace(":", ""), 16) % (2**32)
    rng = random.Random(seed)
    if rng.random() <= 0.6:
        return rng.randint(115, 135)
    return rng.choice([rng.randint(90, 114), rng.randint(136, 160)])

def _simular_poeira(mac_address):
    seed = int(mac_address.replace(":", ""), 16) % (2**32)
    rng = random.Random(seed)
    return rng.randint(0, 100)

def uso_simulado_da_maquina(cpu_base):
    chance_evento = random.random()
    if chance_evento < 0.50:
        cpu = cpu_base + random.uniform(5, 20)
    elif chance_evento < 0.85:
        cpu = cpu_base + random.uniform(60, 85)
    else:
        cpu = cpu_base + random.uniform(90, 130)
    return round(min(cpu, 100.0), 2)

def gerar_linha_financeira_client(dados_fin, linha_client_original):
    mac = linha_client_original["macAddress"]
    cpu_original = linha_client_original["cpuUso"]
    cpu_simulado = uso_simulado_da_maquina(cpu_original)
    cpu = cpu_simulado
    ram = linha_client_original["ramUso"]
    disco = linha_client_original["discoUso"]
    processos = linha_client_original["totalProcessos"]

    alerta_cpu = cpu >= 75
    alerta_ram = ram >= 70
    alerta_disco = disco >= 90

    score_risco = 0
    if cpu >= 95:
        score_risco += 50
    elif cpu >= 85:
        score_risco += 35
    elif cpu >= 75:
        score_risco += 25
    if ram >= 95:
        score_risco += 35
    elif ram >= 85:
        score_risco += 20
    elif ram >= 70:
        score_risco += 10
    if disco >= 95:
        score_risco += 30
    elif disco >= 90:
        score_risco += 20
    if processos >= 700:
        score_risco += 20
    elif processos >= 550:
        score_risco += 10
    score_risco = min(score_risco, 100)

    if score_risco >= 70:
        severidade = "CRITICO"
    elif score_risco >= 40:
        severidade = "ALTO"
    elif score_risco >= 15:
        severidade = "MODERADO"
    else:
        severidade = "NORMAL"

    if score_risco == 0:
        minutos_downtime = random.randint(1, 3)
    else:
        minutos_downtime = int(score_risco * random.uniform(0.45, 1.25))
    minutos_downtime = max(minutos_downtime, 1)

    horas_offline = minutos_downtime / 60
    exames_perdidos = dados_fin["examesPorHora"] * horas_offline
    perda_indisponibilidade = exames_perdidos * dados_fin["valorExame"]

    impacto_lentidao = ((cpu * 0.65) + (ram * 0.35)) / 100
    perda_lentidao = (dados_fin["valorExame"] * dados_fin["examesPorHora"]
                      * impacto_lentidao * horas_offline * 0.45)
    perda_total = perda_indisponibilidade + perda_lentidao

    uptime_real = 100 - ((minutos_downtime / 43200) * 100)
    if severidade == "CRITICO":
        uptime_real -= random.uniform(2.0, 4.5)
    elif severidade == "ALTO":
        uptime_real -= random.uniform(0.7, 2.0)
    elif severidade == "MODERADO":
        uptime_real -= random.uniform(0.1, 0.8)
    uptime_real = max(uptime_real, 0)

    status_sla = "CONFORME" if uptime_real >= dados_fin["metaSLA"] else "VIOLADO"

    multa_sla = 0
    if status_sla == "VIOLADO":
        multa_sla = random.uniform(500, 2500)

    custo_preditiva = random.uniform(700, 1800)
    fator_risco = score_risco / 100
    custo_potencial_falha = (dados_fin["custoCorretiva"] + multa_sla) * fator_risco
    valor_evitado = max(custo_potencial_falha - custo_preditiva, 0)
    perda_residual = max(perda_total - valor_evitado, 0)
    margem_operacional = 0.38
    lucro_preservado = valor_evitado * margem_operacional
    saude_operacional = round(max(100 - ((cpu * 0.30) + (ram * 0.20) + (disco * 0.10)), 0), 2)

    return {
        "financeiroDashboard": {
            "metricas": {
                "cpuOriginal": round(cpu_original, 2),
                "cpuSimulado": round(cpu_simulado, 2),
                "ramUso": round(ram, 2),
                "discoUso": round(disco, 2),
                "totalProcessos": processos,
            },
            "indicadores": {
                "scoreRisco": score_risco,
                "severidade": severidade,
                "saudeOperacional": saude_operacional,
            },
            "financeiro": {
                "downtimeMinutos": minutos_downtime,
                "perdaIndisponibilidade": round(perda_indisponibilidade, 2),
                "perdaLentidao": round(perda_lentidao, 2),
                "perdaTotal": round(perda_total, 2),
                "multaSLA": round(multa_sla, 2),
                "custoPotencialFalha": round(custo_potencial_falha, 2),
                "custoPreditiva": round(custo_preditiva, 2),
                "valorEvitado": round(valor_evitado, 2),
                "perdaResidual": round(perda_residual, 2),
                "lucroPreservado": round(lucro_preservado, 2),
            },
            "alertas": {
                "cpu": alerta_cpu,
                "ram": alerta_ram,
                "disco": alerta_disco,
            },
            "sla": {
                "conformidade": round(uptime_real, 2),
                "meta": dados_fin["metaSLA"],
                "status": status_sla,
            },
        }
    }

def _agrupar_por_mac(linhas_client):
    grupos = defaultdict(list)
    for linha in linhas_client:
        grupos[linha["macAddress"]].append(linha)
    for mac in grupos:
        grupos[mac].sort(key=lambda x: x["horario"])
    return grupos

def gerar_kpis(linhas_client):
    grupos = _agrupar_por_mac(linhas_client)
    ultimos = {mac: registros[-1] for mac, registros in grupos.items() if registros}

    kpi1_mac = max(ultimos, key=lambda mac: ultimos[mac]["ramUso"])
    machine_mais_critica = {
        "macAddress": kpi1_mac,
        "empresa": ultimos[kpi1_mac]["empresa"],
        "ramUso": ultimos[kpi1_mac]["ramUso"],
        "ramUsoBruto": ultimos[kpi1_mac]["ramUsoBruto"],
    }

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

def buscar_nome(cursor, mac_address):
    cursor.execute(
        """
        SELECT numeroSerie FROM maquina WHERE macAddress = %s
        """,
        (mac_address,),
    )
    resultado = cursor.fetchone()
    return resultado[0] if resultado else None

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
        JOIN maquina m ON cm.fkMaquina = m.macAddress
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

def gerar_linha_trusted(cursor, linha):
    horas = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mac_address = linha["macAddress"]
    empresa = buscar_empresa(cursor, mac_address)
    nomeMaquina = buscar_nome(cursor, mac_address)

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
        "nomeMaquina": nomeMaquina,
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

# -------------------------------------------------------------------
# Construção do registro enriquecido (Andrei + Anna unificados)
# -------------------------------------------------------------------

def construir_registro_cliente(trusted_row, limites_mac, financeiro_mac, historico_2h_mac):
    mac = trusted_row["macAddress"]
    nomeMaquina = trusted_row["nomeMaquina"]
    cpu_uso = float(trusted_row["cpuPorcentagem"])
    ram_uso = float(trusted_row["porcentagemRam"])
    ram_bruto = float(trusted_row["ramUsada"])
    disco_bruto = float(trusted_row["discoUsado"])
    disco_uso = float(trusted_row["porcentagemDisco"])

    limite_cpu = limites_mac.get("cpu")
    limite_ram = limites_mac.get("ram")
    limite_disco = limites_mac.get("disco")

    alerta_cpu = limite_cpu is not None and cpu_uso > limite_cpu
    alerta_ram = limite_ram is not None and ram_uso > limite_ram
    alerta_disco = limite_disco is not None and disco_uso > limite_disco

    # Métricas históricas (últimas 2h)
    media_cpu_2h = round(historico_2h_mac["cpuPorcentagem"].mean(), 2)
    media_ram_2h = round(historico_2h_mac["porcentagemRam"].mean(), 2)
    media_disco_2h = round(historico_2h_mac["porcentagemDisco"].mean(), 2)

    desvio_cpu = historico_2h_mac["cpuPorcentagem"].std()
    desvio_cpu = round(desvio_cpu, 2) if pd.notna(desvio_cpu) else 0

    desvio_ram = historico_2h_mac["porcentagemRam"].std()
    desvio_ram = round(desvio_ram, 2) if pd.notna(desvio_ram) else 0

    desvio_disco = historico_2h_mac["porcentagemDisco"].std()
    desvio_disco = round(desvio_disco, 2) if pd.notna(desvio_disco) else 0

    # Classificações
    status_cpu = classificarStatusAtual(cpu_uso, limite_cpu)
    status_ram = classificarStatusAtual(ram_uso, limite_ram)
    status_disco = classificarStatusAtual(disco_uso, limite_disco)

    oscilacao_cpu = classificarOscilacao(cpu_uso, media_cpu_2h, desvio_cpu)
    oscilacao_ram = classificarOscilacao(ram_uso, media_ram_2h, desvio_ram)

    degradacao_cpu = classificarDegradacao(media_cpu_2h, limite_cpu)
    degradacao_ram = classificarDegradacao(media_ram_2h, limite_ram)
    degradacao_disco = classificarDegradacao(media_disco_2h, limite_disco)

    # Regressão linear (numpy puro)
    previsao_cpu = regressaoLinear(historico_2h_mac, "cpuPorcentagem")
    previsao_ram = regressaoLinear(historico_2h_mac, "porcentagemRam")
    previsao_disco = regressaoLinear(historico_2h_mac, "porcentagemDisco")

    # Índice de saúde (João)
    penalidade_cpu = penalidadeSaudeComponente(status_cpu, oscilacao_cpu, degradacao_cpu, previsao_cpu["a"])
    penalidade_ram = penalidadeSaudeComponente(status_ram, oscilacao_ram, degradacao_ram, previsao_ram["a"])
    penalidade_disco = penalidadeSaudeComponente(status_disco, None, None, previsao_disco["a"]) #!

    saude = 100 - (penalidade_cpu + penalidade_ram + penalidade_disco)
    saude_str = f"{saude:.2f} / 100"

    # Dashboard financeira 
    linha_base = {
        "macAddress": mac,
        "horario": str(trusted_row["horas"]),
        "cpuUso": cpu_uso,
        "ramUso": ram_uso,
        "discoUso": disco_uso,
        "totalProcessos": trusted_row["totalProcessos"],
    }
    financeiro_dashboard = gerar_linha_financeira_client(financeiro_mac, linha_base)

    # Financeiro SLA 
    minutos_downtime = 45 if cpu_uso < 2 else 0
    horas_offline = minutos_downtime / 60
    fin = financeiro_mac
    perda_indisponibilidade = horas_offline * fin["valorExame"] * fin["examesPorHora"]

    if alerta_cpu or alerta_ram:
        lucro_retido = max(fin["custoCorretiva"] - 450.0, 0.0) if fin["custoCorretiva"] > 0 else 0.0
        perda_indisponibilidade += 0.25 * fin["valorExame"] * fin["examesPorHora"]
    else:
        lucro_retido = 0.0

    uptime_real = 100 - ((minutos_downtime / 43200) * 100)
    if alerta_cpu:
        uptime_real -= 2.5
    status_sla = "CONFORME" if uptime_real >= fin["metaSLA"] else "VIOLADO"

    # Ambiental
    corrente = _simular_corrente(mac)
    poeira = _simular_poeira(mac)

    record = {
        "empresa": trusted_row["empresa"],
        "macAddress": mac,
        "nomeMaquina": nomeMaquina,
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
        "disco": {
            "uso": disco_uso,
            "limite": limite_disco,
            "status": status_disco,
            "degradacao": degradacao_disco,
            "previsao": previsao_disco,
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
            "alertaDisco": alerta_disco,
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
        # Campos para KPIs agregadas (Caio)
        "ramUso": ram_uso,
        "ramUsoBruto": ram_bruto,
        "discoUsoBruto": disco_bruto,
    }

    # Merge do dashboard financeiro da Anna
    record.update(financeiro_dashboard)
    return record

# -------------------------------------------------------------------
# Lambda principal
# -------------------------------------------------------------------
def lambda_handler(event, context):
    logger.info("Iniciando ETL unificado")

    response = s3.get_object(Bucket=BUCKET, Key=RAW_KEY)
    df_raw = pd.read_csv(response["Body"])
    if df_raw.empty:
        logger.info("Arquivo raw vazio")
        return {"statusCode": 200, "body": "Arquivo raw vazio"}
    
    df_raw = df_raw.drop_duplicates(subset=["macAddress", "horario"])
    df_raw = df_raw.reset_index(drop=True)

    conn = conectar_mysql()
    cursor = conn.cursor()

    try:
        # Carrega trusted existente
        try:
            resp = s3.get_object(Bucket=BUCKET, Key=TRUSTED_KEY)
            df_trusted = pd.read_csv(resp["Body"])
            logger.info("Trusted carregado com %d registros", len(df_trusted))
        except Exception:
            logger.info("Nenhum trusted anterior encontrado, iniciando novo")
            df_trusted = pd.DataFrame()

        # Gera novas linhas trusted
        novas_trusted = [gerar_linha_trusted(cursor, linha) for _, linha in df_raw.iterrows()]
        df_novas = pd.DataFrame(novas_trusted)
        df_trusted = pd.concat([df_trusted, df_novas], ignore_index=True)

        # Últimas 2h
        
        df_trusted["horas"] = pd.to_datetime(df_trusted["horas"], errors="coerce")
        df_trusted = df_trusted[df_trusted["horas"] >= pd.Timestamp.now() - pd.Timedelta(hours=2)]
        df_trusted = df_trusted.reset_index(drop=True)

        # Sem duplicatas. 
        df_trusted = df_trusted.drop_duplicates(subset=["macAddress", "horas"])
        df_trusted = df_trusted.reset_index(drop=True)

        # Persiste trusted no S3
        trusted_buffer = io.StringIO()
        df_trusted.to_csv(trusted_buffer, index=False)
        s3.put_object(
            Bucket=BUCKET, Key=TRUSTED_KEY,
            Body=trusted_buffer.getvalue().encode("utf-8"),
            ContentType="text/csv",
        )

        # Consultas em lote
        macs_novos = df_novas["macAddress"].unique().tolist()
        limites = buscar_limites_em_lote(cursor, macs_novos)
        dados_financeiros = buscar_dados_financeiros_em_lote(cursor, macs_novos)

        # Histórico 2h
        agora = pd.Timestamp.now()
        limite_2h = agora - pd.Timedelta(hours=2)
        ultimas_2h = df_trusted[pd.to_datetime(df_trusted["horas"]) >= limite_2h]

        # Constrói registros enriquecidos
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

        # KPIs agregadas, ranking e histórico (Caio)
        kpis = gerar_kpis(client_records)
        ranking = gerar_ranking(client_records)
        historico = gerar_historico(client_records)

        output = {
            "maquinas": client_records,
            "kpis": kpis,
            "ranking": ranking,
            "historico": historico,
        }

        client_json = json.dumps(output, ensure_ascii=False, indent=2)
        s3.put_object(
            Bucket=BUCKET, Key=CLIENT_KEY,
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

lambda_handler(None, None)