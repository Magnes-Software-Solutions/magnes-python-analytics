import datetime, time
import pandas as pd
import os
#import mysql.connector
import boto3

# Importação das bibliotecas necessárias para a leitura das métricas coletadas do sistema, assim como sua análise.

arquivo = "dadostratados.csv"
last_index = 0

bucket = "s3-projeto-magnes-2026.04.09"
caminho_s3 = "trusted/dadosTratados.csv"

s3 = boto3.client("s3")

# Horário de última leitura para evitar processar os mesmos dados repetidamente.
last_horario = None

# conexão MySQL (ajuste)
#conn = mysql.connector.connect(
#    host="SEU_HOST",
#    user="SEU_USER",
#    password="SUA_SENHA",
#    database="SEU_BANCO"
#)

#cursor = conn.cursor()

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

        # busca id da maquina
        #cursor.execute(
        #    "SELECT id FROM maquina WHERE mac_address = %s",
        #    (macAddress,)
        #)
        #resultado = cursor.fetchone()
        #id_maquina = resultado[0] if resultado else None

        cpuPorcentagem = ultimo["cpuPorcentagem"]
        cpuNucleosFisicos = ultimo["cpuNucleosFisicos"]
        cpuNucleosLogicos = ultimo["cpuNucleosLogicos"]

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
        #    "idMaquina": [id_maquina],
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
            "porcentagemDisco": [porcentagemDisco]
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
ID Máquina: {"maquina1"}
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

Horário: {horas}
======================================
""")
    
    # envia arquivo atualizado para o S3 (sobrescreve)
    s3.upload_file(arquivo, bucket, caminho_s3)
    print("CSV tratado atualizado no S3")

    # atualiza ponteiro
    last_index = len(df)
    
    # Espera 10 segundos antes de realizar a próxima leitura.
    time.sleep(10)


