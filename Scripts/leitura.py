
import datetime
import io
import json
import logging
import os

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
def buscar_dados(cursor):

    sql = """
        SELECT 
            monitoramento.cpu,
            monitoramento.ram,
            monitoramento.disco,
            monitoramento.dataHora,
            maquina.tipoModelo AS nome_maquina,
            maquina.fkRedeHospital

        FROM monitoramento

        JOIN maquina
            ON monitoramento.fkMaquina = maquina.macAddress

        INNER JOIN (
            
            SELECT 
                fkMaquina,
                MAX(idMonitoramento) AS ultimo

            FROM monitoramento

            GROUP BY fkMaquina

        ) ultimoMonitoramento

            ON monitoramento.idMonitoramento = ultimoMonitoramento.ultimo
    """

    cursor.execute(sql)

    resultado = cursor.fetchall()

    lista = []

    for linha in resultado:

        lista.append({

            "cpu": linha[0],
            "ram": linha[1],
            "disco": linha[2],
            "dataHora": str(linha[3]),
            "nome_maquina": linha[4],
            "fkRedeHospital": linha[5]

        })

    return lista


def buscar_limites(cursor, mac_address):
    cursor.execute(
        """
        SELECT
            MAX(CASE WHEN c.tipoComponente = 'Processador' THEN cm.limite END) as limiteCPU,
            MAX(CASE WHEN c.tipoComponente = 'Memória' THEN cm.limite END) as limiteRAM,
            MAX(CASE WHEN c.tipoComponente = 'Armazenamento' THEN cm.limite END) as limiteDisco
        FROM componente_maquina cm
        JOIN componente c
            ON cm.fkComponente = c.idComponente
        JOIN maquina m
            ON cm.fkMaquina = m.idMaquina
        WHERE m.macAddress = %s
        """,
        (mac_address,),
    )

    limites = cursor.fetchone()

    if not limites:
        return None, None, None

    return limites[0], limites[1], limites[2]


# ----------------------------------------------------------

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





def gerar_linha_client(cursor, linha):

    limite_cpu, limite_ram, limite_disco = buscar_limites(
        cursor,
        linha["macAddress"]
    )

    alerta_cpu = (
        limite_cpu is not None and
        linha["cpuPorcentagem"] > limite_cpu
    )

    alerta_ram = (
        limite_ram is not None and
        linha["porcentagemRam"] > limite_ram
    )

    alerta_disco = (
        limite_disco is not None and
        linha["porcentagemDisco"] > limite_disco
    )

    return {

        "empresa": linha["empresa"],

        "macAddress": linha["macAddress"],

        "horario": str(linha["horas"]),

        "cpu": linha["cpuPorcentagem"],

        "ram": linha["porcentagemRam"],

        "disco": linha["porcentagemDisco"],

        "ramUsada": linha["ramUsada"],

        "discoUsado": linha["discoUsado"],

        "limiteCPU": limite_cpu,

        "limiteRAM": limite_ram,

        "limiteDisco": limite_disco,

        "alertaCPU": alerta_cpu,

        "alertaRAM": alerta_ram,

        "alertaDisco": alerta_disco,

        "totalProcessos": linha["totalProcessos"]
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

            linha_client = gerar_linha_client(cursor, linha_trusted)
            linhas_client.append(linha_client)

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