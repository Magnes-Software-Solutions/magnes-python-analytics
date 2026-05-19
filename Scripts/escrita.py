import psutil
import datetime
import time
import pandas as pd
import os
import boto3
import uuid


# CONFIGURAÇÕES


arquivo = "dadosBrutos.csv"

bucket = "s3-projeto-magnes-2026.04.09"

caminho_s3 = "raw/dadosBrutos.csv"

# Cliente S3
s3 = boto3.client(
    's3',
    aws_access_key_id="",
    aws_secret_access_key="",
    aws_session_token=""
)


# FUNÇÃO PARA PEGAR O MAC ADDRESS


def pegar_mac():

    mac = ':'.join([
        '{:02x}'.format((uuid.getnode() >> ele) & 0xff)
        for ele in range(0, 8 * 6, 8)
    ][::-1])

    return mac

# Guarda o MAC da máquina
mac_address = pegar_mac()

print(f"MAC da máquina: {mac_address}")


# LOOP PRINCIPAL


while True:

    # Horário atual
    horario = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    
    # CPU
    

    cpuPorcentagem = psutil.cpu_percent()

    cpuNucleosFisicos = psutil.cpu_count(logical=False)

    cpuNucleosLogicos = psutil.cpu_count()

    cpuTempoUser = psutil.cpu_times().user

    cpuTempoSistema = psutil.cpu_times().system

    cpuTempoInativo = psutil.cpu_times().idle

    
    # RAM
    

    memoria = psutil.virtual_memory()

    ramUsada = memoria.used

    ramLivre = memoria.available

    ramTotal = memoria.total

    # Porcentagem da RAM
    porcentagemRam = memoria.percent

    
    # DISCO
    

    disco = psutil.disk_usage("C:\\")

    discoUsado = disco.used

    discoLivre = disco.free

    discoTotal = disco.total

    # Porcentagem do disco
    porcentagemDisco = disco.percent

    
    # PROCESSOS
    

    totalProcessos = len(psutil.pids())

    
    # MOSTRA NO TERMINAL
    

    print(f"""
======================================

Horário: {horario}

MAC: {mac_address}

--------------------------------------

CPU: {cpuPorcentagem}%

RAM: {porcentagemRam}%

DISCO: {porcentagemDisco}%

--------------------------------------

Processos: {totalProcessos}

======================================
""")

    
    # DADOS QUE VÃO PARA O CSV
    

        # DADOS QUE VÃO PARA O CSV
    

    dados = {

        # Identificação da máquina
        "macAddress": [mac_address],

        # Horário da coleta
        "horario": [horario],

        
        # CPU
        

        # Uso da CPU em %
        "cpuUso": [cpuPorcentagem],

        # Núcleos físicos
        "cpuNucleosFisicos": [cpuNucleosFisicos],

        # Núcleos lógicos
        "cpuNucleosLogicos": [cpuNucleosLogicos],

        # Tempo em user
        "cpuTempoUser": [cpuTempoUser],

        # Tempo em sistema
        "cpuTempoSistema": [cpuTempoSistema],

        # Tempo ocioso
        "cpuTempoInativo": [cpuTempoInativo],


        
        # RAM
        

        # RAM usada em bytes
        "ramUsoBruto": [ramUsada],

        # RAM livre
        "ramLivre": [ramLivre],

        # RAM total
        "ramTotal": [ramTotal],

        # Uso da RAM em %
        "ramUso": [porcentagemRam],


        
        # DISCO
        

        # Disco usado em bytes
        "discoUsoBruto": [discoUsado],

        # Disco livre
        "discoLivre": [discoLivre],

        # Disco total
        "discoTotal": [discoTotal],

        # Uso do disco em %
        "discoUso": [porcentagemDisco],


        
        # PROCESSOS
        

        "totalProcessos": [totalProcessos]
    }

    
    # CRIA DATAFRAME
    

    df = pd.DataFrame(dados)

    
    # ESCREVE NO CSV
    

    # Se o arquivo não existir, cria com cabeçalho
    if not os.path.exists(arquivo):

        df.to_csv(
            arquivo,
            index=False
        )

    # Se existir, adiciona nova linha
    else:

        df.to_csv(
            arquivo,
            mode="a",
            header=False,
            index=False
        )

    
    # ENVIA PARA O S3
    

    s3.upload_file(
        arquivo,
        bucket,
        caminho_s3
    )

    print("CSV atualizado no S3")

    # Espera 10 segundos
    time.sleep(10)