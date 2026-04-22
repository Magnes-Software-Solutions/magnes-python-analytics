import datetime, time
import pandas as pd
import os
import mysql.connector
import boto3

# Importação das bibliotecas necessárias para a leitura das métricas coletadas do sistema, assim como sua análise.

arquivo = "dadosTratados.csv"
arquivo_client = "dadosPerfeitos.csv"

last_index = 0
last_index_trusted = 0

bucket = "s3-projeto-magnes-2026.04.09"
caminho_s3 = "trusted/dadosTratados.csv"
caminho_client = "client/dadosPerfeitos.csv"

s3 = boto3.client("s3")

# Horário de última leitura para evitar processar os mesmos dados repetidamente.
last_horario = None

# conexão MySQL (ajuste)
conn = mysql.connector.connect(
    host="localhost",
    user="magnes",
    password="Magnes#2026",
    database="magnes"
)

cursor = conn.cursor()

while True:

    # lê CSV bruto
    response = s3.get_object(Bucket="s3-projeto-magnes-2026.04.09", Key = "raw/dadosBrutos.csv")
    df = pd.read_csv(response["Body"])

    # pega somente linhas novas
    novos = df.iloc[last_index:]

    if novos.empty:
        print("sem novos dados")
        time.sleep(10)
        continue

    for _, ultimo in novos.iterrows():

        horas = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        macAddress = ultimo["macAddress"]

        cursor.execute("""
            SELECT r.razaoSocial
            FROM maquina m
            JOIN redeHospital r
            ON m.fkRedeHospital = r.idRedeHospital
            WHERE m.macAddress = %s
            """, (macAddress,))

        resultado = cursor.fetchone()

        if resultado:       
            empresa = resultado[0]
        else:
            empresa = None


        cpuPorcentagem = ultimo["cpuPorcentagem"]
        cpuNucleosFisicos = ultimo["cpuNucleosFisicos"]
        cpuNucleosLogicos = ultimo["cpuNucleosLogicos"]
        total_processos = ultimo["totalProcessos"]

        cpuTempoUser = round(ultimo["cpuTempoUser"] / 60)
        cpuTempoSistema = ultimo["cpuTempoSistema"]
        cpuTempoInativo = ultimo["cpuTempoInativo"]

        ramLivre  = round(ultimo["ramLivre"]  / 1024**3, 2)
        ramUsada  = round(ultimo["ramUsada"]  / 1024**3, 2)
        ramTotal  = round(ultimo["ramTotal"]  / 1024**3, 2)

        discoLivre = round(ultimo["discoLivre"] / 1024**3, 2)
        discoUsado = round(ultimo["discoUsado"] / 1024**3, 2)
        discoTotal = round(ultimo["discoTotal"] / 1024**3, 2)

        porcentagemRam = round((ramUsada / ramTotal) * 100, 2)
        porcentagemDisco = round((discoUsado / discoTotal) * 100, 2)



        dados_resultados = {
            "empresa": [empresa],
            "macAddress": [macAddress],
            "horas": [horas],
            "cpuPorcentagem": [cpuPorcentagem],
            "cpuNucleosFisicos": [cpuNucleosFisicos],
            "cpuNucleosLogicos": [cpuNucleosLogicos],
            "cpuTempoUser": [cpuTempoUser],
            "cpuTempoSistema": [cpuTempoSistema],
            "cpuTempoInativo": [cpuTempoInativo],
            "ramLivre": [ramLivre],
            "ramUsada": [ramUsada],
            "ramTotal": [ramTotal],
            "discoLivre": [discoLivre],
            "discoUsado": [discoUsado],
            "discoTotal": [discoTotal],
            "porcentagemRam": [porcentagemRam],
            "porcentagemDisco": [porcentagemDisco],
            "totalProcessos": [total_processos]
        }

        
    # Cria o arquivo CSV se ele não existir, ou anexa os dados se ele já existir.
    df_resultados = pd.DataFrame(dados_resultados)
    if not os.path.exists(arquivo):
        df_resultados.to_csv(arquivo, index=False)
    else:
        df_resultados.to_csv(arquivo, mode="a", header=False, index=False)


    # Imprime os dados mais recentes e as médias no console.
    print(f"""      
======================================
EMPRESA: {empresa}          
MAC: {macAddress} 
          
CPU Porcentagem: {cpuPorcentagem}%
CPU Núcleos Físicos: {cpuNucleosFisicos}
CPU Núcleos Lógicos: {cpuNucleosLogicos}

CPU Tempo Usuário: {cpuTempoUser}
CPU Tempo Sistema: {cpuTempoSistema}
CPU Tempo Inativo: {cpuTempoInativo}

----------------------------------

RAM Usada: {ramUsada} GB
RAM Total: {ramTotal} GB
RAM Livre: {ramLivre} GB

----------------------------------

Disco Usado: {discoUsado} GB
Disco Total: {discoTotal} GB
Disco Livre: {discoLivre} GB

----------------------------------

Porcentagem RAM Usada: {porcentagemRam}%

Porcentagem Disco Usado: {porcentagemDisco}%

Total de Processos Executados: {total_processos}

Horário: {horas}
======================================
""")
    

    # envia arquivo atualizado para trusted no S3 (sobrescreve)
    s3.upload_file(arquivo, bucket, caminho_s3)
    print("CSV tratado atualizado no S3 trusted")

    # lê dadosTratados.CSV do S3 
    response_trusted = s3.get_object(Bucket="s3-projeto-magnes-2026.04.09", Key = "trusted/dadosTratados.csv")
    df_trusted = pd.read_csv(response_trusted["Body"], on_bad_lines="skip")

    # pega somente linhas novas do trusted
    novos_trusted = df_trusted.iloc[last_index_trusted:]

    if not novos_trusted.empty:

        for _, linha in novos_trusted.iterrows():

            empresa = linha["empresa"]
            macAddress = linha["macAddress"]
            horas = linha["horas"]

            cpuPorcentagem = linha["cpuPorcentagem"]
            porcentagemRam = linha["porcentagemRam"]
            porcentagemDisco = linha["porcentagemDisco"]

            ramUsada = linha["ramUsada"]
            discoUsado = linha["discoUsado"]
            total_processos = linha["totalProcessos"]

            cursor.execute("""
                SELECT 
                MAX(CASE WHEN c.tipoComponente = 'Processador' THEN cm.limite END) as limiteCPU,
                MAX(CASE WHEN c.tipoComponente = 'Memória' THEN cm.limite END) as limiteRAM,
                MAX(CASE WHEN c.tipoComponente = 'Armazenamento' THEN cm.limite END) as limiteDisco
                FROM componente_maquina cm
                JOIN componente c ON cm.fkComponente = c.idComponente
                WHERE cm.fkMaquina = %s
                """, (macAddress,))

            limites = cursor.fetchone()

            limiteCPU = limites[0] if limites else None
            limiteRAM = limites[1] if limites else None
            limiteDisco = limites[2] if limites else None

            alertaCPU = False
            alertaRAM = False
            alertaDisco = False

            if cpuPorcentagem > limiteCPU:
                alertaCPU = True

            if porcentagemRam > limiteRAM:
                alertaRAM = True

            if porcentagemDisco > limiteDisco:
                alertaDisco = True

            dados_client = {
            "empresa": [empresa],
            "macAddress": [macAddress],
            "horario": [horas],
            "cpuUso": [cpuPorcentagem],
            "ramUsoBruto": [ramUsada],
            "ramUso": [porcentagemRam],
            "discoUsoBruto": [discoUsado],
            "discoUso": [porcentagemDisco],
            "limiteCPU": [limiteCPU],
            "limiteRAM": [limiteRAM],
            "limiteDisco": [limiteDisco],
            "alertaCPU": [alertaCPU],
            "alertaRAM": [alertaRAM],
            "alertaDisco": [alertaDisco],
            "totalProcessos": [total_processos]
            }
    
            df_client = pd.DataFrame(dados_client)

            if not os.path.exists(arquivo_client):
                df_client.to_csv(arquivo_client, index=False)
            else:
                df_client.to_csv(arquivo_client, mode="a", header=False, index=False)

            print(f"""
==============================
EMPRESA: {empresa}
MAC: {macAddress}

RAM VALOR: {ramUsada}GB
DISCO VALOR: {discoUsado}GB
CPU: {cpuPorcentagem}% | ALERTA: {alertaCPU}
RAM: {porcentagemRam}% | ALERTA: {alertaRAM}
DISCO: {porcentagemDisco}% | ALERTA: {alertaDisco}
LIMITECPU: {limiteCPU}%
LIMITERAM: {limiteRAM}%
LIMITEDISCO: {limiteDisco}%  
PROCESSOS TOTAIS: {total_processos}          

Horário: {horas}
==============================
""")
    
    # envia arquivo atualizado para client no S3 (sobrescreve)
    s3.upload_file(arquivo_client, bucket, caminho_client)
    print("CSV perfeito atualizado no S3 client")

    # atualiza ponteiros
    last_index = len(df)
    last_index_trusted = len(df_trusted)

    # Espera 10 segundos antes de realizar a próxima leitura.
    time.sleep(10)


