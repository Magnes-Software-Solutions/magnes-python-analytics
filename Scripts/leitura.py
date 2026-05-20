import datetime
import io
import json
import logging
import os
import random

import boto3
import mysql.connector
import pandas as pd


logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

BUCKET = os.environ.get("BUCKET", "s3-projeto-magnes-2026.04.09")
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


def buscar_empresa(cursor, mac_address):
    cursor.execute(
        """
        SELECT r.razaoSocial
        FROM maquina m
        JOIN redeHospital r
            ON m.fkRedeHospital = r.idRedeHospital
        WHERE m.macAddress = %s
        """,
        (mac_address,),
    )

    resultado = cursor.fetchone()
    return resultado[0] if resultado else None


def buscar_limites(cursor, mac_address):
    cursor.execute(
        """
        SELECT
            MAX(CASE WHEN c.tipoComponente = 'Processador' THEN cm.limite END) as limiteCPU,
            MAX(CASE WHEN c.tipoComponente = 'Memoria' THEN cm.limite END) as limiteRAM,
            MAX(CASE WHEN c.tipoComponente = 'Armazenamento' THEN cm.limite END) as limiteDisco
        FROM componente_maquina cm
        JOIN componente c
            ON cm.fkComponente = c.idComponente
        JOIN maquina m
            ON cm.fkMaquina = m.macAddress
        WHERE m.macAddress = %s
        """,
        (mac_address,),
    )

    limites = cursor.fetchone()

    if not limites:
        return None, None, None

    return limites[0], limites[1], limites[2]


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


def gerar_linha_client(cursor, linha):
    limite_cpu, limite_ram, limite_disco = buscar_limites(cursor, linha["macAddress"])

    alerta_cpu = limite_cpu is not None and linha["cpuPorcentagem"] > limite_cpu
    alerta_ram = limite_ram is not None and linha["porcentagemRam"] > limite_ram
    alerta_disco = limite_disco is not None and linha["porcentagemDisco"] > limite_disco

    return {
        "empresa": linha["empresa"],
        "macAddress": linha["macAddress"],
        "horario": str(linha["horas"]),
        "cpuUso": linha["cpuPorcentagem"],
        "ramUsoBruto": linha["ramUsada"],
        "ramUso": linha["porcentagemRam"],
        "discoUsoBruto": linha["discoUsado"],
        "discoUso": linha["porcentagemDisco"],
        "limiteCPU": limite_cpu,
        "limiteRAM": limite_ram,
        "limiteDisco": limite_disco,
        "alertaCPU": alerta_cpu,
        "alertaRAM": alerta_ram,
        "alertaDisco": alerta_disco,
        "totalProcessos": linha["totalProcessos"],
    }

# Dashboard Financeira - Individual (Anna)

def buscar_dados_negocio_maquina(cursor, mac_address):
    cursor.execute(
        """
        SELECT
            m.valorMedioExame,
            m.examesPorHora,
            m.metaSLA,
            m.custoCorretiva,
            e.bairro,
            e.cidade,
            e.numeroEstabelecimento,
            e.cep
        FROM maquina m
        JOIN enderecoHospital e
            ON m.fkEnderecoHospital = e.idEnderecoHospital
        WHERE m.macAddress = %s
        """,
        (mac_address,)
    )

    resultado = cursor.fetchone()

    if resultado:
        return {
            "valorExame": float(resultado[0] or 0),
            "examesPorHora": int(resultado[1] or 0),
            "metaSLA": float(resultado[2] or 100),
            "custoCorretiva": float(resultado[3] or 0),

            "bairro": resultado[4] or "Não Cadastrado",
            "cidade": resultado[5] or "Não Cadastrado",
            "numero": resultado[6] or "N/A",
            "cep": resultado[7] or "N/A"
        }

    return {
        "valorExame": 0.0,
        "examesPorHora": 0,
        "metaSLA": 100.0,
        "custoCorretiva": 0.0,

        "bairro": "Não Cadastrado",
        "cidade": "Não Cadastrado",
        "numero": "N/A",
        "cep": "N/A"
    }

def uso_simulado_da_maquina(cpu_base):
    cpu_ajustada = cpu_base + random.uniform(5.0, 18.0)
    
    if random.random() < 0.40:
        return min(cpu_ajustada * 4.5, 100.0)
    
    return min(cpu_ajustada, 100.0)

def gerar_linha_financeira_client(cursor, linha_client_original):

    import random

    mac = linha_client_original["macAddress"]
    dados_fin = buscar_dados_negocio_maquina(cursor, mac)

    cpu = uso_simulado_da_maquina(
        linha_client_original["cpuUso"]
    )

    ram = linha_client_original["ramUso"]
    disco = linha_client_original["discoUso"]
    processos = linha_client_original["totalProcessos"]

    # alertas

    alerta_cpu = cpu >= 75
    alerta_ram = ram >= 70
    alerta_disco = disco >= 90

    # score de risco

    score_risco = 0

    # CPU
    if cpu >= 95:
        score_risco += 50
    elif cpu >= 85:
        score_risco += 35
    elif cpu >= 75:
        score_risco += 25

    # RAM
    if ram >= 95:
        score_risco += 35
    elif ram >= 85:
        score_risco += 20
    elif ram >= 70:
        score_risco += 10

    # DISCO
    if disco >= 95:
        score_risco += 30
    elif disco >= 90:
        score_risco += 20

    # PROCESSOS
    if processos >= 700:
        score_risco += 20
    elif processos >= 550:
        score_risco += 10

    # limite máximo
    score_risco = min(score_risco, 100)

    # severidade

    if score_risco >= 70:
        severidade = "CRITICO"
    elif score_risco >= 40:
        severidade = "ALTO"
    elif score_risco >= 15:
        severidade = "MODERADO"
    else:
        severidade = "NORMAL"

    # downtime baseado no risco

    if score_risco == 0:
        minutos_downtime = random.randint(1, 3)
    else:
        minutos_downtime = int(score_risco * random.uniform(0.45, 1.25))

    minutos_downtime = max(minutos_downtime, 1)

    horas_offline = minutos_downtime / 60

    # impacto operacional

    exames_perdidos = (dados_fin["examesPorHora"] * horas_offline)

    perda_indisponibilidade = (exames_perdidos * dados_fin["valorExame"])

    # impacto da lentidão e a sua perda

    impacto_lentidao = (((cpu * 0.65) + (ram * 0.35)) / 100)

    perda_lentidao = (dados_fin["valorExame"] * dados_fin["examesPorHora"] * impacto_lentidao * horas_offline * 0.45)

    # perda total

    perda_total = (perda_indisponibilidade + perda_lentidao)

    # manutenção preditiva

    custo_preditiva = random.uniform(700, 1800)
    fator_risco = score_risco / 100

    custo_potencial_falha = (dados_fin["custoCorretiva"] * fator_risco)

    valor_evitado = max(custo_potencial_falha - custo_preditiva, 0)
    perda_residual = max(perda_total - valor_evitado, 0)

    # lucro preservado

    margem_operacional = 0.38

    lucro_preservado = (valor_evitado * margem_operacional)

    # SLA 

    uptime_real = 100 - ((minutos_downtime / 43200) * 100)

    # penalização adicional conforme severidade

    if severidade == "CRITICO":
        uptime_real -= random.uniform(2.0, 4.5)
    elif severidade == "ALTO":
        uptime_real -= random.uniform(0.7, 2.0)
    elif severidade == "MODERADO":
        uptime_real -= random.uniform(0.1, 0.8)

    uptime_real = max(uptime_real, 0)
    status_sla = ("CONFORME"
        if uptime_real >= dados_fin["metaSLA"]
        else "VIOLADO"
    )

    # saude operacional da máquina

    saude_operacional = round(max(100 - ((cpu * 0.30) + (ram * 0.20) + (disco * 0.10)), 0), 2)

    # return do json tratado

    return {
        "macAddress": mac,
        "horario": linha_client_original["horario"],
        "indicadores": {
            "scoreRisco": score_risco,
            "severidade": severidade,
            "saudeOperacional": saude_operacional
        },

        "financeiro": {
            "downtimeMinutos": minutos_downtime,
            "perdaIndisponibilidade": round(perda_indisponibilidade, 2),
            "perdaLentidao": round(perda_lentidao,2),
            "perdaTotal": round(perda_total, 2),
            "custoPotencialFalha": round(custo_potencial_falha, 2),
            "custoPreditiva": round(custo_preditiva, 2),
            "valorEvitado": round(valor_evitado, 2),
            "perdaResidual": round(perda_residual, 2),
            "lucroPreservado": round(lucro_preservado, 2)
        },
        "alertas": {
            "cpu": alerta_cpu,
            "ram": alerta_ram,
            "disco": alerta_disco
        },
        "sla": {
            "conformidade": round(uptime_real, 2),
            "meta": dados_fin["metaSLA"],
            "status": status_sla
        }
    }

# Finalização - Individual (Anna)
    
def lambda_handler(event, context):
    logger.info("Iniciando ETL")

    response = s3.get_object(Bucket=BUCKET, Key=RAW_KEY)
    df_raw = pd.read_csv(response["Body"])

    if df_raw.empty:
        logger.info("Arquivo raw vazio")
        return {"statusCode": 200, "body": "Arquivo raw vazio"}

    conn = conectar_mysql()
    cursor = conn.cursor()

    try:
        linhas_trusted = []
        linhas_client = []

        for _, linha in df_raw.iterrows():
            linha_trusted = gerar_linha_trusted(cursor, linha)
            linhas_trusted.append(linha_trusted)

            linha_base = gerar_linha_client(cursor, linha_trusted)

            linha_enriquecida = gerar_linha_financeira_client(cursor, linha_base)

            linhas_client.append(linha_enriquecida)

        df_trusted = pd.DataFrame(linhas_trusted)

        trusted_buffer = io.StringIO()
        df_trusted.to_csv(trusted_buffer, index=False)
        s3.put_object(
            Bucket=BUCKET,
            Key=TRUSTED_KEY,
            Body=trusted_buffer.getvalue().encode("utf-8"),
            ContentType="text/csv",
        )

        client_json = json.dumps(linhas_client, ensure_ascii=False, indent=2)
        s3.put_object(
            Bucket=BUCKET,
            Key=CLIENT_KEY,
            Body=client_json.encode("utf-8"),
            ContentType="application/json",
        )

        logger.info("ETL finalizada. Linhas processadas: %s", len(df_raw))

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "mensagem": "ETL finalizada com sucesso",
                    "linhasProcessadas": len(df_raw),
                    "trusted": TRUSTED_KEY,
                    "client": CLIENT_KEY,
                },
                ensure_ascii=False,
            ),
        }

    finally:
        cursor.close()
        conn.close()