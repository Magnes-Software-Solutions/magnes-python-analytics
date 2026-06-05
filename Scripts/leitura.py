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

BUCKET   = os.environ.get("BUCKET",       "magnes-solutions")
RAW_KEY  = os.environ.get("RAW_KEY",      "raw/dadosBrutos.csv")
RAW_PREFIX   = os.environ.get("RAW_PREFIX",   "raw/")
TRUSTED_KEY  = os.environ.get("TRUSTED_KEY",  "trusted/dadosTratados.csv")
CLIENT_KEY   = os.environ.get("CLIENT_KEY",   "client/dadosPerfeitos.json")


def normalizar_mac(mac: str) -> str:
    return mac.replace("-", ":").lower()


def conectar_mysql():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
    )


def regressaoLinear(ultimas2hMaquina, componente, limite):
    if ultimas2hMaquina.empty or len(ultimas2hMaquina) < 4:
        return {"a": 0, "b": 0, "r2": 0, "reta": [], "previsaoLimite": "Dados insuficientes"}

    df = ultimas2hMaquina.copy()
    df["horas"] = pd.to_datetime(df["horas"], errors="coerce")
    df[componente] = pd.to_numeric(df[componente], errors="coerce")
    df = df.dropna(subset=["horas", componente]).sort_values("horas")

    if df.empty or len(df) < 2:
        return {"a": 0, "b": 0, "r2": 0, "reta": [], "previsaoLimite": "Dados insuficientes"}

    intervaloMinutos = 10
    df["x"] = (df["horas"] - df["horas"].min()).dt.total_seconds() / (60 * intervaloMinutos)
    x = df["x"].values
    y = df[componente].values

    mascara = np.isfinite(x) & np.isfinite(y)
    x, y = x[mascara], y[mascara]

    if len(x) < 2:
        return {"a": 0, "b": 0, "r2": 0, "reta": [], "previsaoLimite": "Dados inválidos"}

    if np.all(x == x[0]):
        return {"a": 0, "b": round(float(np.mean(y)), 2), "reta": [], "previsaoLimite": "Sem variação temporal"}

    try:
        a, b = np.polyfit(x, y, 1)
        a, b = float(a), float(b)
        yPrevisto = a * x + b
        ss_res = np.sum((y - yPrevisto) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = float(0 if ss_tot == 0 else 1 - (ss_res / ss_tot))
    except Exception as erro:
        logger.warning("Erro regressão linear: %s", erro)
        return {"a": 0, "b": 0, "r2": 0, "reta": [], "previsaoLimite": "Erro regressão"}

    previsao = "Sem previsão"
    reta = []
    limiteA = {"cpuPorcentagem": 0.3, "porcentagemRam": 0.1, "porcentagemDisco": 0.03}
    if a > limiteA[componente] and r2 >= 0.7:
        if y[-1] >= limite:
            previsao = "Já está crítico"
        else:
            prevCriticidade = (limite - b) / a
            if prevCriticidade < x.max():
                previsao = "Limite já atingido"
            elif 0 <= prevCriticidade <= 8640:
                data_prev = pd.to_datetime(df["horas"].min()) + pd.to_timedelta(prevCriticidade * intervaloMinutos, unit="m")
                previsao = "≈" + str(data_prev)

            reta = [
                {"x": str(df["horas"].min()), "y": round(a * x.min() + b, 2)},
                {"x": str(df["horas"].max()), "y": round(a * x.max() + b, 2)},
            ]
    return {"a": a, "b": round(b, 2), "r2": round(r2, 2), "reta": reta, "previsaoLimite": previsao}


def classificarStatusAtual(valorAtual, limite):
    if limite is None:
        return "Limite desconhecido"
    if valorAtual < limite * 0.8:
        return "Estável"
    elif valorAtual < limite:
        return "Anormal"
    return "Crítico"


def classificarOscilacao(valorAtual, mediaHistorica, desvio):
    if desvio is None or np.isnan(desvio) or desvio <= 0:
        return "Sem variação"
    desvio = max(desvio, 3)
    distancia = abs(valorAtual - mediaHistorica)
    if distancia <= desvio:
        return "Baixa (abaixo de 1σ)"
    elif distancia <= desvio * 2:
        return "Média (entre 1σ e 2σ)"
    elif distancia <= desvio * 3:
        return "Alta  (entre 2σ e 3σ)"
    return "Severa (acima de 3σ)"


def classificarDegradacao(mediaHistorica, limite):
    if limite is None:
        return "Limite desconhecido"
    distancia = limite - mediaHistorica
    if distancia >= 15:
        return "Recuperação"
    elif distancia > 5:
        return "Degradação Média"
    elif distancia <= 5:
        return "Degradação Alta"
    return "Desconhecido (dados insuficientes)"


def penalidadeSaudeComponente(statusAtual, oscilacao, degradacao, previsao):
    a  = previsao["a"]
    r2 = previsao["r2"]
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
    if r2 >= 0.7:
        penalidade += 3.7 if a > 5 else (1.5 if a > 2 else 0)
    return penalidade


def _simular_imagem(mac_address):
    TIPOS_DICOM = ["T1-weighted", "T2-weighted", "FLAIR", "DWI", "T1 contrast", "BOLD fMRI"]
    mac_limpo = mac_address.replace("-", ":").replace(":", "")
    seed = int(mac_limpo, 16) % (2**32)
    rng = random.Random(seed)
    return {
        "tamanhoGB": round(rng.uniform(1.5, 8.0), 2),
        "tipoDicom": rng.choice(TIPOS_DICOM),
    }


def _simular_corrente():
    if random.random() <= 0.6:
        return random.randint(115, 135)
    return random.choice([random.randint(90, 114), random.randint(136, 160)])


def _simular_poeira():
    r = random.random()
    if r <= 0.60:
        return random.randint(0, 11)
    elif r <= 0.85:
        return random.randint(12, 34)
    return random.randint(35, 75)


def uso_simulado_da_maquina(cpu_base):
    chance = random.random()
    if chance < 0.50:
        cpu = cpu_base + random.uniform(5, 20)
    elif chance < 0.85:
        cpu = cpu_base + random.uniform(20, 70)
    else:
        cpu = cpu_base + random.uniform(70, 100)
    return round(min(cpu, 100.0), 2)


def simular_tendencia_7dias(df_trusted_atual):
    if df_trusted_atual.empty:
        return []

    cpu_atual = df_trusted_atual['cpuPorcentagem'].mean() if 'cpuPorcentagem' in df_trusted_atual.columns else random.uniform(40, 70)
    ram_atual = df_trusted_atual['porcentagemRam'].mean() if 'porcentagemRam' in df_trusted_atual.columns else random.uniform(30, 60)
    disco_atual = df_trusted_atual['porcentagemDisco'].mean() if 'porcentagemDisco' in df_trusted_atual.columns else random.uniform(40, 75)
    maquinas_atuais = df_trusted_atual['macAddress'].nunique() if 'macAddress' in df_trusted_atual.columns else random.randint(3, 8)

    alertas_atuais = len(df_trusted_atual[
        (df_trusted_atual['cpuPorcentagem'] > 80) |
        (df_trusted_atual['porcentagemRam'] > 70) |
        (df_trusted_atual['porcentagemDisco'] > 90)
    ]) if all(col in df_trusted_atual.columns for col in ['cpuPorcentagem', 'porcentagemRam', 'porcentagemDisco']) else random.randint(0, 8)

    tendencia = []
    hoje = pd.Timestamp.now().date()

    for dias_atras in range(6, -1, -1):
        data = hoje - datetime.timedelta(days=dias_atras)
        fator_variacao = 1 + (dias_atras * 0.08)

        variacao_cpu = random.uniform(-8, 12) * fator_variacao
        cpu_dia = min(max(cpu_atual - (dias_atras * 1.5) + variacao_cpu, 20), 95)

        variacao_ram = random.uniform(-5, 8) * fator_variacao
        ram_dia = min(max(ram_atual - (dias_atras * 0.8) + variacao_ram, 15), 92)

        variacao_disco = random.uniform(-3, 5) * fator_variacao
        disco_dia = min(max(disco_atual - (dias_atras * 2.0) + variacao_disco, 30), 88)

        alertas_base = alertas_atuais - (dias_atras * random.randint(0, 2))
        if cpu_dia > 75 or ram_dia > 65:
            alertas_base += random.randint(1, 3)
        alertas_dia = max(0, min(alertas_base + random.randint(-2, 3), 15))

        cves_dia = max(0, random.randint(1, 5) - (dias_atras * random.randint(0, 1)))
        if dias_atras > 3:
            cves_dia += random.randint(0, 2)

        if dias_atras == 0:
            cobertura = random.randint(60, 100)
        elif dias_atras == 1:
            cobertura = random.randint(85, 100)
        else:
            cobertura = random.randint(95, 100)

        maquinas_dia = max(1, maquinas_atuais + random.randint(-1, 0))

        tendencia.append({
            "data": data.strftime("%Y-%m-%d"),
            "cpu_media": round(cpu_dia, 1),
            "cpu_max": round(cpu_dia + random.uniform(5, 15), 1),
            "ram_media": round(ram_dia, 1),
            "ram_max": round(ram_dia + random.uniform(3, 10), 1),
            "disco_media": round(disco_dia, 1),
            "disco_max": round(disco_dia + random.uniform(2, 8), 1),
            "total_alertas": alertas_dia,
            "cves_ativas": cves_dia,
            "cobertura_coleta": cobertura,
            "total_maquinas": maquinas_dia,
            "coletas_realizadas": random.randint(120, 144)
        })

    return tendencia


def gerar_linha_financeira_client(dados_fin, linha_client_original):
    cpu_original = linha_client_original["cpuUso"]
    cpu_simulado = uso_simulado_da_maquina(cpu_original)
    cpu   = cpu_simulado
    ram   = linha_client_original["ramUso"]
    disco = linha_client_original["discoUso"]
    processos = linha_client_original["totalProcessos"]

    alerta_cpu   = cpu   >= 75
    alerta_ram   = ram   >= 70
    alerta_disco = disco >= 90

    score_risco = 0
    if cpu >= 95:       score_risco += 50
    elif cpu >= 85:     score_risco += 35
    elif cpu >= 75:     score_risco += 25
    if ram >= 95:       score_risco += 35
    elif ram >= 85:     score_risco += 20
    elif ram >= 70:     score_risco += 10
    if disco >= 95:     score_risco += 30
    elif disco >= 90:   score_risco += 20
    if processos >= 700:    score_risco += 20
    elif processos >= 550:  score_risco += 10
    score_risco = min(score_risco, 100)

    if score_risco >= 70:   severidade = "CRITICO"
    elif score_risco >= 40: severidade = "ALTO"
    elif score_risco >= 15: severidade = "MODERADO"
    else:                   severidade = "NORMAL"

    minutos_downtime = random.randint(1, 3) if score_risco == 0 else int(score_risco * random.uniform(0.45, 1.25))
    minutos_downtime = max(minutos_downtime, 1)

    horas_offline = minutos_downtime / 60
    perda_indisponibilidade = dados_fin["examesPorHora"] * horas_offline * dados_fin["valorExame"]
    impacto_lentidao = ((cpu * 0.65) + (ram * 0.35)) / 100
    perda_lentidao = dados_fin["valorExame"] * dados_fin["examesPorHora"] * impacto_lentidao * horas_offline * 0.45
    perda_total = perda_indisponibilidade + perda_lentidao

    uptime_real = 100 - ((minutos_downtime / 43200) * 100)
    if severidade == "CRITICO":     uptime_real -= random.uniform(2.0, 4.5)
    elif severidade == "ALTO":      uptime_real -= random.uniform(0.7, 2.0)
    elif severidade == "MODERADO":  uptime_real -= random.uniform(0.1, 0.8)
    uptime_real = max(uptime_real, 0)

    status_sla = "CONFORME" if uptime_real >= dados_fin["metaSLA"] else "VIOLADO"
    multa_sla  = random.uniform(500, 2500) if status_sla == "VIOLADO" else 0

    custo_preditiva     = random.uniform(700, 1800)
    fator_risco         = score_risco / 100
    custo_potencial_falha = (dados_fin["custoCorretiva"] + multa_sla) * fator_risco
    valor_evitado       = max(custo_potencial_falha - custo_preditiva, 0)
    perda_residual      = max(perda_total - valor_evitado, 0)
    lucro_preservado    = valor_evitado * 0.38
    saude_operacional   = round(max(100 - ((cpu * 0.30) + (ram * 0.20) + (disco * 0.10)), 0), 2)

    return {
        "financeiroDashboard": {
            "metricas": {
                "cpuOriginal":   round(cpu_original, 2),
                "cpuSimulado":   round(cpu_simulado, 2),
                "ramUso":        round(ram, 2),
                "discoUso":      round(disco, 2),
                "totalProcessos": processos,
            },
            "indicadores": {
                "scoreRisco":       score_risco,
                "severidade":       severidade,
                "saudeOperacional": saude_operacional,
            },
            "financeiro": {
                "downtimeMinutos":       minutos_downtime,
                "perdaIndisponibilidade": round(perda_indisponibilidade, 2),
                "perdaLentidao":         round(perda_lentidao, 2),
                "perdaTotal":            round(perda_total, 2),
                "multaSLA":              round(multa_sla, 2),
                "custoPotencialFalha":   round(custo_potencial_falha, 2),
                "custoPreditiva":        round(custo_preditiva, 2),
                "valorEvitado":          round(valor_evitado, 2),
                "perdaResidual":         round(perda_residual, 2),
                "lucroPreservado":       round(lucro_preservado, 2),
            },
            "alertas": {"cpu": alerta_cpu, "ram": alerta_ram, "disco": alerta_disco},
            "sla": {
                "conformidade": round(uptime_real, 2),
                "meta":         dados_fin["metaSLA"],
                "status":       status_sla,
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
        "empresa":    ultimos[kpi1_mac]["empresa"],
        "ramUso":     ultimos[kpi1_mac]["ramUso"],
        "ramUsoBruto": ultimos[kpi1_mac]["ramUsoBruto"],
    }

    variacoes = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        variacoes[mac] = {
            "variacao":  round(abs(registros[-1]["ramUso"] - registros[-2]["ramUso"]), 2),
            "ultimo":    registros[-1]["ramUso"],
            "penultimo": registros[-2]["ramUso"],
            "empresa":   registros[-1]["empresa"],
        }
    maior_variacao = None
    if variacoes:
        k = max(variacoes, key=lambda mac: variacoes[mac]["variacao"])
        maior_variacao = {"macAddress": k, **variacoes[k]}

    tendencias = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        tendencias[mac] = {
            "delta":   round(registros[-1]["ramUso"] - registros[-2]["ramUso"], 2),
            "ramUso":  registros[-1]["ramUso"],
            "empresa": registros[-1]["empresa"],
        }
    pior_tendencia = None
    if tendencias:
        k = max(tendencias, key=lambda mac: tendencias[mac]["delta"])
        pior_tendencia = {"macAddress": k, **tendencias[k]}

    crescimentos = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        delta = registros[-1]["discoUsoBruto"] - registros[-2]["discoUsoBruto"]
        if delta > 0:
            crescimentos[mac] = {
                "tamanhoGB": round(delta, 2),
                "empresa":   registros[-1]["empresa"],
                "simulado":  False,
            }

    if crescimentos:
        k = max(crescimentos, key=lambda mac: crescimentos[mac]["tamanhoGB"])
        imagem_pesada = {
            "macAddress": k,
            "empresa":    crescimentos[k]["empresa"],
            "tamanhoGB":  crescimentos[k]["tamanhoGB"],
            "tipoDicom":  _simular_imagem(k)["tipoDicom"],
            "simulado":   False,
        }
    else:
        logger.warning("KPI 4: sem crescimento de disco detectado. Usando simulação.")
        k = max(ultimos, key=lambda mac: _simular_imagem(mac)["tamanhoGB"])
        sim = _simular_imagem(k)
        imagem_pesada = {
            "macAddress": k,
            "empresa":    ultimos[k]["empresa"],
            "tamanhoGB":  sim["tamanhoGB"],
            "tipoDicom":  sim["tipoDicom"],
            "simulado":   True,
        }

    return {
        "geradoEm":           datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "machineMaisCritica": machine_mais_critica,
        "maiorVariacaoRam":   maior_variacao,
        "piorTendencia":      pior_tendencia,
        "imagemMaisPesada":   imagem_pesada,
    }


def gerar_ranking(linhas_client):
    grupos = _agrupar_por_mac(linhas_client)
    ranking = []
    for mac, registros in grupos.items():
        u = registros[-1]
        ranking.append({
            "macAddress":  mac,
            "empresa":     u["empresa"],
            "ramUso":      u["ramUso"],
            "ramUsoBruto": u["ramUsoBruto"],
            "horario":     u["horario"],
        })
    ranking.sort(key=lambda x: x["ramUso"], reverse=True)
    for i, item in enumerate(ranking, start=1):
        item["posicao"] = i
    return ranking


def gerar_historico(linhas_client, limite=2):
    grupos = _agrupar_por_mac(linhas_client)
    return [
        {
            "macAddress": mac,
            "empresa":    registros[-1]["empresa"],
            "registros": [
                {"horario": r["horario"], "ramUso": r["ramUso"], "ramUsoBruto": r["ramUsoBruto"]}
                for r in registros[-limite:]
            ],
        }
        for mac, registros in grupos.items()
    ]


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
    cursor.execute("SELECT numeroSerie FROM maquina WHERE macAddress = %s", (mac_address,))
    resultado = cursor.fetchone()
    return resultado[0] if resultado else None


def buscar_limites_em_lote(cursor, lista_macs):
    if not lista_macs:
        return {}
    placeholders = ",".join(["%s"] * len(lista_macs))
    cursor.execute(
        f"""
        SELECT m.macAddress,
               MAX(CASE WHEN c.tipoComponente = 'Processador'   THEN cm.limite END) AS limiteCPU,
               MAX(CASE WHEN c.tipoComponente = 'Memória'        THEN cm.limite END) AS limiteRAM,
               MAX(CASE WHEN c.tipoComponente = 'Armazenamento' THEN cm.limite END) AS limiteDisco
        FROM componente_maquina cm
        JOIN componente c ON cm.fkComponente = c.idComponente
        JOIN maquina m    ON cm.fkMaquina    = m.macAddress
        WHERE m.macAddress IN ({placeholders})
        GROUP BY m.macAddress
        """,
        lista_macs,
    )
    return {mac: {"cpu": cpu, "ram": ram, "disco": disco} for mac, cpu, ram, disco in cursor.fetchall()}


def buscar_dados_financeiros_em_lote(cursor, lista_macs):
    if not lista_macs:
        return {}
    placeholders = ",".join(["%s"] * len(lista_macs))
    cursor.execute(
        f"""
        SELECT m.macAddress, m.valorMedioExame, m.examesPorHora, m.metaSLA,
               m.custoCorretiva, e.bairro, e.cidade, e.numeroEstabelecimento, e.cep
        FROM maquina m
        JOIN enderecoHospital e ON m.fkEnderecoHospital = e.idEnderecoHospital
        WHERE m.macAddress IN ({placeholders})
        """,
        lista_macs,
    )
    dados = {}
    for mac, valor, exames, meta, custo, bairro, cidade, num, cep in cursor.fetchall():
        dados[mac] = {
            "valorExame":   float(valor  or 0),
            "examesPorHora": exames or 0,
            "metaSLA":      float(meta   or 100),
            "custoCorretiva": float(custo or 0),
            "bairro": bairro, "cidade": cidade, "numero": num, "cep": cep,
        }
    return dados


def gerar_linha_trusted(cursor, linha):
    mac_address = normalizar_mac(linha["macAddress"])
    empresa     = buscar_empresa(cursor, mac_address)
    nomeMaquina = buscar_nome(cursor, mac_address)

    def gb(val): return round(val / 1024**3, 2)

    ram_usada = gb(linha["ramUsada"])
    ram_total = gb(linha["ramTotal"])
    disco_usado = gb(linha["discoUsado"])
    disco_total = gb(linha["discoTotal"])

    return {
        "empresa":           empresa,
        "macAddress":        mac_address,
        "nomeMaquina":       nomeMaquina,
        "horas":             linha["horario"],
        "cpuPorcentagem":    linha["cpuPorcentagem"],
        "cpuNucleosFisicos": linha["cpuNucleosFisicos"],
        "cpuNucleosLogicos": linha["cpuNucleosLogicos"],
        "cpuTempoUser":      round(linha["cpuTempoUser"] / 60),
        "cpuTempoSistema":   linha["cpuTempoSistema"],
        "cpuTempoInativo":   linha["cpuTempoInativo"],
        "ramLivre":          gb(linha["ramLivre"]),
        "ramUsada":          ram_usada,
        "ramTotal":          ram_total,
        "discoLivre":        gb(linha["discoLivre"]),
        "discoUsado":        disco_usado,
        "discoTotal":        disco_total,
        "porcentagemRam":    round((ram_usada  / ram_total)   * 100, 2) if ram_total   else 0,
        "porcentagemDisco":  round((disco_usado / disco_total) * 100, 2) if disco_total else 0,
        "totalProcessos":    linha["totalProcessos"],
    }


def construir_registro_cliente(trusted_row, limites_mac, financeiro_mac, historico_2h_mac):
    mac         = trusted_row["macAddress"]
    cpu_uso     = float(trusted_row["cpuPorcentagem"])
    ram_uso     = float(trusted_row["porcentagemRam"])
    ram_bruto   = float(trusted_row["ramUsada"])
    disco_bruto = float(trusted_row["discoUsado"])
    disco_uso   = float(trusted_row["porcentagemDisco"])

    limite_cpu   = limites_mac.get("cpu")
    limite_ram   = limites_mac.get("ram")
    limite_disco = limites_mac.get("disco")

    alerta_cpu   = limite_cpu   is not None and cpu_uso   > limite_cpu
    alerta_ram   = limite_ram   is not None and ram_uso   > limite_ram
    alerta_disco = limite_disco is not None and disco_uso > limite_disco

    def media(col): return round(historico_2h_mac[col].mean(), 2)
    def desvio(col):
        d = historico_2h_mac[col].std()
        return round(d, 2) if pd.notna(d) else 0

    media_cpu   = media("cpuPorcentagem")
    media_ram   = media("porcentagemRam")
    media_disco = media("porcentagemDisco")

    status_cpu   = classificarStatusAtual(cpu_uso,   limite_cpu)
    status_ram   = classificarStatusAtual(ram_uso,   limite_ram)
    status_disco = classificarStatusAtual(disco_uso, limite_disco)

    oscilacao_cpu = classificarOscilacao(cpu_uso, media_cpu, desvio("cpuPorcentagem"))
    oscilacao_ram = classificarOscilacao(ram_uso, media_ram, desvio("porcentagemRam"))

    degradacao_cpu   = classificarDegradacao(media_cpu,   limite_cpu)
    degradacao_ram   = classificarDegradacao(media_ram,   limite_ram)
    degradacao_disco = classificarDegradacao(media_disco, limite_disco)

    previsao_cpu   = regressaoLinear(historico_2h_mac, "cpuPorcentagem", limite_cpu)
    previsao_ram   = regressaoLinear(historico_2h_mac, "porcentagemRam", limite_ram)
    previsao_disco = regressaoLinear(historico_2h_mac, "porcentagemDisco", limite_disco)

    saude = 100 - (
        penalidadeSaudeComponente(status_cpu,   oscilacao_cpu, degradacao_cpu,   previsao_cpu)
        + penalidadeSaudeComponente(status_ram, oscilacao_ram, degradacao_ram,   previsao_ram)
        + penalidadeSaudeComponente(status_disco, None,        degradacao_disco, previsao_disco)
    )

    linha_base = {
        "macAddress":    mac,
        "horario":       str(trusted_row["horas"]),
        "cpuUso":        cpu_uso,
        "ramUso":        ram_uso,
        "discoUso":      disco_uso,
        "totalProcessos": trusted_row["totalProcessos"],
    }
    financeiro_dashboard = gerar_linha_financeira_client(financeiro_mac, linha_base)

    fin = financeiro_mac
    minutos_downtime = 45 if cpu_uso < 2 else 0
    horas_offline    = minutos_downtime / 60
    perda_indisp     = horas_offline * fin["valorExame"] * fin["examesPorHora"]

    if alerta_cpu or alerta_ram:
        lucro_retido  = max(fin["custoCorretiva"] - 450.0, 0.0) if fin["custoCorretiva"] > 0 else 0.0
        perda_indisp += 0.25 * fin["valorExame"] * fin["examesPorHora"]
    else:
        lucro_retido = 0.0

    uptime_real = 100 - ((minutos_downtime / 43200) * 100)
    if alerta_cpu:
        uptime_real -= 2.5
    status_sla = "CONFORME" if uptime_real >= fin["metaSLA"] else "VIOLADO"

    corrente   = _simular_corrente()
    poeira     = _simular_poeira()

    record = {
        "empresa":     trusted_row["empresa"],
        "macAddress":  mac,
        "nomeMaquina": trusted_row["nomeMaquina"],
        "horario":     str(trusted_row["horas"]),
        "cpu": {
            "uso": round(cpu_uso, 1), "limite": limite_cpu,
            "status": status_cpu, "oscilacao": oscilacao_cpu,
            "degradacao": degradacao_cpu, "previsao": previsao_cpu,
        },
        "ram": {
            "uso": round(ram_uso, 1), "limite": limite_ram,
            "status": status_ram, "oscilacao": oscilacao_ram,
            "degradacao": degradacao_ram, "previsao": previsao_ram,
        },
        "disco": {
            "uso": round(disco_uso, 1), "limite": limite_disco,
            "status": status_disco, "degradacao": degradacao_disco,
            "previsao": previsao_disco,
        },
        "indiceSaude": f"{saude:.1f} / 100",
        "financeiro": {
            "localizacao": {
                "cidade": fin["cidade"], "bairro": fin["bairro"],
                "numero": fin["numero"], "cep":    fin["cep"],
            },
            "alertaCPU":               alerta_cpu,
            "alertaRAM":               alerta_ram,
            "alertaDisco":             alerta_disco,
            "kpiPerdaIndisponibilidade": round(perda_indisp, 2),
            "custoCorretivaPotencial":  fin["custoCorretiva"],
            "economiaPreditiva":        450.0,
            "kpiLucroRetido":           round(lucro_retido, 2),
            "kpiConformidadeSLA":       round(max(uptime_real, 0.0), 2),
            "metaSLA":                  fin["metaSLA"],
            "statusSLA":                status_sla,
            "confiabilidadeAtivo":      round(100 - cpu_uso, 2),
        },
        "ambiente": {
            "corrente":          corrente,
            "poeira":            poeira,
            "percentualVoltagem": round(min((corrente / 135) * 100, 100.0), 1),
        },
        "ramUso":       ram_uso,
        "ramUsoBruto":  ram_bruto,
        "discoUsoBruto": disco_bruto,
    }
    record.update(financeiro_dashboard)
    return record


def carregar_dados_raw():
    dfs = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=BUCKET, Prefix=RAW_PREFIX):
        for obj in page.get("Contents", []):
            key          = obj["Key"]
            nome_arquivo = os.path.basename(key)

            valido = (
                nome_arquivo == "dadosBrutos.csv"
                or (nome_arquivo.startswith("dadosBrutos") and nome_arquivo.endswith(".csv"))
            )
            if not valido:
                continue

            try:
                response = s3.get_object(Bucket=BUCKET, Key=key)
                df = pd.read_csv(response["Body"])
                if not df.empty:
                    dfs.append(df)
                    logger.info("Arquivo carregado: %s (%d linhas)", key, len(df))
            except Exception as e:
                logger.error("Falha ao ler %s: %s", key, e)
                continue

    if not dfs:
        logger.warning("Nenhum arquivo por macAddress encontrado. Tentando fallback: %s", RAW_KEY)
        try:
            response = s3.get_object(Bucket=BUCKET, Key=RAW_KEY)
            return pd.read_csv(response["Body"])
        except Exception as e:
            logger.error("Fallback também falhou: %s", e)
            return pd.DataFrame()

    df_raw = pd.concat(dfs, ignore_index=True)
    df_raw = df_raw.drop_duplicates(subset=["macAddress", "horario"]).reset_index(drop=True)
    logger.info("Total raw carregado (sem duplicatas): %d registros", len(df_raw))
    return df_raw


def lambda_handler(event, context):
    logger.info("Iniciando ETL unificado")

    df_raw = carregar_dados_raw()
    if df_raw.empty:
        logger.info("Arquivo raw vazio")
        return {"statusCode": 200, "body": "Arquivo raw vazio"}

    df_raw = df_raw.drop_duplicates(subset=["macAddress", "horario"]).reset_index(drop=True)

    conn   = conectar_mysql()
    cursor = conn.cursor()

    try:
        try:
            resp       = s3.get_object(Bucket=BUCKET, Key=TRUSTED_KEY)
            df_trusted = pd.read_csv(resp["Body"])
            logger.info("Trusted carregado com %d registros", len(df_trusted))
        except Exception:
            logger.info("Nenhum trusted anterior encontrado, iniciando novo")
            df_trusted = pd.DataFrame()

        novas_trusted = [gerar_linha_trusted(cursor, linha) for _, linha in df_raw.iterrows()]
        df_novas   = pd.DataFrame(novas_trusted)
        df_trusted = pd.concat([df_trusted, df_novas], ignore_index=True)

        df_trusted["horas"] = pd.to_datetime(df_trusted["horas"], errors="coerce", utc=False)

        ts_max = df_trusted["horas"].max()
        if pd.isna(ts_max):
            logger.warning("Nenhum timestamp válido. Usando now() como fallback.")
            ts_max = pd.Timestamp.now()

        df_trusted_7dias = df_trusted[df_trusted["horas"] >= ts_max - pd.Timedelta(days=7)].copy()

        df_trusted = df_trusted[df_trusted["horas"] >= ts_max - pd.Timedelta(hours=2)]
        df_trusted = df_trusted.drop_duplicates(subset=["macAddress", "horas"]).reset_index(drop=True)
        logger.info("Trusted após filtro 2h (ref=%s): %d registros", ts_max, len(df_trusted))

        buf = io.StringIO()
        df_trusted_7dias.to_csv(buf, index=False)
        s3.put_object(Bucket=BUCKET, Key=TRUSTED_KEY, Body=buf.getvalue().encode("utf-8"), ContentType="text/csv")

        macs_novos        = df_novas["macAddress"].unique().tolist()
        limites           = buscar_limites_em_lote(cursor, macs_novos)
        dados_financeiros = buscar_dados_financeiros_em_lote(cursor, macs_novos)

        df_novas["horas"] = pd.to_datetime(df_novas["horas"])
        df_ultimos = df_novas.sort_values("horas").groupby("macAddress", as_index=False).tail(1)
        df_historico = (
            df_trusted[df_trusted["macAddress"].isin(macs_novos)]
            .sort_values("horas")
            .groupby("macAddress", as_index=False)
            .tail(2)
        )

        fin_padrao = {"valorExame": 0.0, "examesPorHora": 0, "metaSLA": 100.0,
                      "custoCorretiva": 0.0, "bairro": "N/A", "cidade": "N/A", "numero": "N/A", "cep": "N/A"}

        client_records = []
        for _, trusted_row in df_ultimos.iterrows():
            mac      = trusted_row["macAddress"]
            hist_mac = df_trusted[df_trusted["macAddress"] == mac]
            record   = construir_registro_cliente(
                trusted_row,
                limites.get(mac, {}),
                dados_financeiros.get(mac, fin_padrao),
                hist_mac,
            )
            client_records.append(record)

        historico_records = []
        for _, trusted_row in df_historico.iterrows():
            mac = trusted_row["macAddress"]
            hist_mac = df_trusted[df_trusted["macAddress"] == mac]
            record = construir_registro_cliente(
                trusted_row,
                limites.get(mac, {}),
                dados_financeiros.get(mac, fin_padrao),
                hist_mac,
            )
            historico_records.append(record)

        tendencia_7dias = simular_tendencia_7dias(df_trusted_7dias)

        output = {
            "maquinas": client_records,
            "kpis":     gerar_kpis(client_records),
            "ranking":  gerar_ranking(client_records),
            "historico": gerar_historico(historico_records, limite=2),
            "tendencia_7dias": tendencia_7dias,
        }

        s3.put_object(
            Bucket=BUCKET, Key=CLIENT_KEY,
            Body=json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

        logger.info("ETL finalizada. Linhas processadas: %d", len(df_raw))
        return {
            "statusCode": 200,
            "body": json.dumps({
                "mensagem":        "ETL finalizada com sucesso",
                "linhasProcessadas": len(df_raw),
                "client":          CLIENT_KEY,
            }, ensure_ascii=False),
        }

    finally:
        cursor.close()
        conn.close()
