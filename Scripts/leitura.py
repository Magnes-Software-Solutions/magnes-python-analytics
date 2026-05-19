import datetime
import io
import json
import logging
import os

import random                          # bivlioteca adcionada para kpi
from collections import defaultdict   # biblioteca adcionada para kpi

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
KPIS_KEY    = os.environ.get("KPIS_KEY",    "client/caio-kpis.json") # para salvar em um arquivo separado(Não sei se vão deixar)
RANKING_KEY   = os.environ.get("RANKING_KEY",   "client/caio-ranking.json")
HISTORICO_KEY = os.environ.get("HISTORICO_KEY", "client/caio-historico.json")

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


#aqui começa as kpi do caio lindo cheiroso e maravilhoso

# todos os tipos de exame DICOM para a kpi 4
 
 
def _simular_imagem(mac_address):
    TIPOS_DICOM = ["T1-weighted", "T2-weighted", "FLAIR", "DWI", "T1 contrast", "BOLD fMRI"]
    """
    Gera tamanho e tipo de imagem DICOM de forma determinística por MAC.
    Usado como fallback na KPI 4 quando não há variação de disco detectável.
    O macAddress é usado como semente para gerar sempre os mesmos valores para as.
    """
    seed = int(mac_address.replace(":", ""), 16) % (2**32)
    rng  = random.Random(seed)
    return {
        "tamanhoGB": round(rng.uniform(1.5, 8.0), 2),
        "tipoDicom": rng.choice(TIPOS_DICOM),
    }
     # Agrupa por macAddress e ordena por horario em ordem crescente (pra facilitar minha vida)
def _agrupar_por_mac(linhas_client):
    grupos = defaultdict(list)
    for linha in linhas_client:
        grupos[linha["macAddress"]].append(linha)
    for mac in grupos:
        grupos[mac].sort(key=lambda x: x["horario"])
    return grupos


def gerar_kpis(linhas_client):
     
    grupos = _agrupar_por_mac(linhas_client)
   
    # aqui comeca a kpi 1
    ultimos  = {mac: registros[-1] for mac, registros in grupos.items() if registros}
    kpi1_mac = max(ultimos, key=lambda mac: ultimos[mac]["ramUso"])
 
    kpi_maquina_critica = {
        "macAddress":  kpi1_mac,
        "empresa":     ultimos[kpi1_mac]["empresa"],
        "ramUso":      ultimos[kpi1_mac]["ramUso"],      # % de RAM consumida agora
        "ramUsoBruto": ultimos[kpi1_mac]["ramUsoBruto"], # GB consumidos agora
    }

    # aqui termina a kpi 1
 
    # aqui começa a kpi 2 
    variacoes = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        variacao = abs(registros[-1]["ramUso"] - registros[-2]["ramUso"])
        variacoes[mac] = {
            "variacao":  round(variacao, 2),
            "ultimo":    registros[-1]["ramUso"],
            "penultimo": registros[-2]["ramUso"],
            "empresa":   registros[-1]["empresa"],
        }
 
    if variacoes:
        kpi2_mac = max(variacoes, key=lambda mac: variacoes[mac]["variacao"])
        kpi_maior_variacao = {
            "macAddress": kpi2_mac,
            "empresa":    variacoes[kpi2_mac]["empresa"],
            "variacao":   variacoes[kpi2_mac]["variacao"],
            "ultimo":     variacoes[kpi2_mac]["ultimo"],
            "penultimo":  variacoes[kpi2_mac]["penultimo"],
        }
    else:
        logger.warning("KPI 2: nenhum macAddress possui 2 ou mais registros.")
        kpi_maior_variacao = None

    # aqui termina a kpi 2    
 
    # aqi comreça a kpi 3
    tendencias = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        delta = registros[-1]["ramUso"] - registros[-2]["ramUso"]
        tendencias[mac] = {
            "delta":   round(delta, 2),  # positivo = cresceu, negativo = caiu
            "ramUso":  registros[-1]["ramUso"],
            "empresa": registros[-1]["empresa"],
        }
 
    if tendencias:
        kpi3_mac = max(tendencias, key=lambda mac: tendencias[mac]["delta"])
        kpi_pior_tendencia = {
            "macAddress": kpi3_mac,
            "empresa":    tendencias[kpi3_mac]["empresa"],
            "delta":      tendencias[kpi3_mac]["delta"],
            "ramUso":     tendencias[kpi3_mac]["ramUso"],
        }
    else:
        logger.warning("KPI 3: nenhum macAddress possui 2 ou mais registros.")
        kpi_pior_tendencia = None

    # aqui termina a kpi 3    
 
    # aqui começa a kpi 4
    # Usa o crescimento de discoUsoBruto como proxy do tamanho do arquivo DICOM.
    # Se não houver crescimento detectável, cai no fallback.
    crescimentos = {}
    for mac, registros in grupos.items():
        if len(registros) < 2:
            continue
        delta_disco = registros[-1]["discoUsoBruto"] - registros[-2]["discoUsoBruto"]
        if delta_disco > 0:
            crescimentos[mac] = {
                "tamanhoGB": round(delta_disco, 2),
                "empresa":   registros[-1]["empresa"],
                "simulado":  False,
            }
 
    if crescimentos:
        kpi4_mac = max(crescimentos, key=lambda mac: crescimentos[mac]["tamanhoGB"])
        kpi_imagem_pesada = {
            "macAddress": kpi4_mac,
            "empresa":    crescimentos[kpi4_mac]["empresa"],
            "tamanhoGB":  crescimentos[kpi4_mac]["tamanhoGB"],
            "tipoDicom":  _simular_imagem(kpi4_mac)["tipoDicom"],  # tipo simulado
            "simulado":   False,
        }
    else:
        logger.warning("KPI 4: sem crescimento de disco detectado. Usando simulação.")
        kpi4_mac = max(ultimos, key=lambda mac: _simular_imagem(mac)["tamanhoGB"])
        simulacao = _simular_imagem(kpi4_mac)
        kpi_imagem_pesada = {
            "macAddress": kpi4_mac,
            "empresa":    ultimos[kpi4_mac]["empresa"],
            "tamanhoGB":  simulacao["tamanhoGB"],
            "tipoDicom":  simulacao["tipoDicom"],
            "simulado":   True,
        }
    # aqui acaba a 4 kpi
    return {
        "geradoEm":           datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "machineMaisCritica": kpi_maquina_critica,
        "maiorVariacaoRam":   kpi_maior_variacao,
        "piorTendencia":      kpi_pior_tendencia,
        "imagemMaisPesada":   kpi_imagem_pesada,
    }
    # Aqui acaba as KPI do caio lindo maravilhoso

    # Aqui começa o  ranking do caio lindo
def gerar_ranking(linhas_client):

        grupos = _agrupar_por_mac(linhas_client)
 
        ranking = []
        for mac, registros in grupos.items():
            ultimo = registros[-1]
            ranking.append({
                "macAddress":  mac,
                "empresa":     ultimo["empresa"],
                "ramUso":      ultimo["ramUso"],
                "ramUsoBruto": ultimo["ramUsoBruto"],
                "horario":     ultimo["horario"],
        })
 
        ranking.sort(key=lambda x: x["ramUso"], reverse=True)
 
        for i, item in enumerate(ranking, start=1):
            item["posicao"] = i
 
        return ranking
 
    # Aqui termina o ranking

    # Aqui comeca o historico (grafico de linha)
def gerar_historico(linhas_client, limite=20):
    
    grupos = _agrupar_por_mac(linhas_client)
 
    historico = []
    for mac, registros in grupos.items():
        ultimos = registros[-limite:]
        historico.append({
            "macAddress": mac,
            "empresa":    registros[-1]["empresa"],
            "registros": [
                {
                    "horario":     r["horario"],
                    "ramUso":      r["ramUso"],
                    "ramUsoBruto": r["ramUsoBruto"],
                }
                for r in ultimos
            ],
        })

    return historico


    #Aqui termina o Historico(Grafico de linha) 

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

        # para salvar em uma aequivo separado
        kpis = gerar_kpis(linhas_client)
        s3.put_object(
            Bucket=BUCKET,
            Key=KPIS_KEY,
            Body=json.dumps(kpis, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        ranking = gerar_ranking(linhas_client)
        s3.put_object(
            Bucket=BUCKET,
            Key=RANKING_KEY,
            Body=json.dumps(ranking, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        historico = gerar_historico(linhas_client, limite=20)
        s3.put_object(
            Bucket=BUCKET,
            Key=HISTORICO_KEY,
            Body=json.dumps(historico, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        # 

        logger.info("ETL finalizada. Linhas processadas: %s", len(df_raw))

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "mensagem": "ETL finalizada com sucesso",
                    "linhasProcessadas": len(df_raw),
                    "trusted": TRUSTED_KEY,
                    "client": CLIENT_KEY,
                    "kpis": KPIS_KEY, # chave para salvar em um aquivo separado(ver se é isso mesmo ou salvar no client)
                },
                ensure_ascii=False,
            ),
        }

    finally:
        cursor.close()
        conn.close()

