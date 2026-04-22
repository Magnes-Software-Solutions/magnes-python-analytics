import psutil, datetime, time
import pandas as pd
import os
import boto3
import uuid


# Importação das bibliotecas necessárias para a coleta de métricas do sistema.


arquivo = "dadosBrutos.csv"

bucket = "s3-projeto-magnes-2026.04.09"
caminho_s3 = "raw/dadosBrutos.csv"

s3 = boto3.client('s3',
                  aws_access_key_id = "",
                  aws_secret_access_key = "",
                  aws_session_token = ""
                    )

# pega MAC uma vez só
def pegar_mac():
    mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff)
                    for ele in range(0,8*6,8)][::-1])
    return mac

mac_address = pegar_mac()
print(f"MAC da máquina: {mac_address}")

# loop infinito para definir e enviar as métricas para um arquivo CSV a cada 10 segundos.
while True:
    horas = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total_processos = len(psutil.pids())

    cpuPorcentagem = psutil.cpu_percent()
    cpuNucleosFisicos = psutil.cpu_count(logical=False)
    cpuNucleosLogicos = psutil.cpu_count()

    cpuTempoUser = psutil.cpu_times().user
    cpuTempoSistema = psutil.cpu_times().system
    cpuTempoInativo = psutil.cpu_times().idle

    ramLivre = (psutil.virtual_memory().available)
    ramUsada = (psutil.virtual_memory().used)
    ramTotal = (psutil.virtual_memory().total)

    discoLivre = (psutil.disk_usage("C:\\").free)
    discoUsado = (psutil.disk_usage("C:\\").used)
    discoTotal = (psutil.disk_usage("C:\\").total)

    #Imprime as métricas coletadas no terminal.
    print(f"""
|      
|    Horário: {horas}
|    MacAddress: {mac_address},     
======================================
 
CPU Porcentagem: {cpuPorcentagem}%
CPU Núcleos Físicos: {cpuNucleosFisicos}
CPU Núcleos Lógicos: {cpuNucleosLogicos}

CPU Tempo Usuário: {cpuTempoUser}
CPU Tempo Sistema: {cpuTempoSistema}
CPU Tempo Inativo: {cpuTempoInativo}

----------------------------------

RAM Usada: {ramUsada}
RAM Total: {ramTotal} 
RAM Livre: {ramLivre}

----------------------------------

Disco Usado: {discoUsado}
Disco Total: {discoTotal} 
Disco Livre: {discoLivre}

----------------------------------

Total Processos: {total_processos}

======================================
""")
    
    # Definição dos dados a serem escritos no arquivo CSV.
    dados = {"macAddress": [mac_address],"horario": [horas], "cpuPorcentagem": [cpuPorcentagem], "cpuNucleosFisicos": [cpuNucleosFisicos], "cpuNucleosLogicos": [cpuNucleosLogicos], "cpuTempoUser": [cpuTempoUser], "cpuTempoSistema": [cpuTempoSistema], "cpuTempoInativo": [cpuTempoInativo], "ramUsada": [ramUsada], "ramTotal": [ramTotal], "ramLivre": [ramLivre], "discoUsado": [discoUsado], "discoTotal": [discoTotal], "discoLivre": [discoLivre], "totalProcessos": [total_processos]}

    #Criação do dataframe usando a biblioteca pandas.
    df = pd.DataFrame(dados)


    # Cria o arquivo CSV se ele não existir, ou anexa os dados se ele já existir.
    if not os.path.exists(arquivo):
        df.to_csv(arquivo, index=False)
    else:
        df.to_csv(arquivo, mode="a", header=False, index=False)

    # envia (sobrescreve) no S3
    s3.upload_file(arquivo, bucket, caminho_s3)

    print("CSV atualizado no S3")
    
    time.sleep(10)

    