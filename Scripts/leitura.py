import datetime, time
import pandas as pd
import os
# Importação das bibliotecas necessárias para a leitura das métricas coletadas do sistema, assim como sua análise.

arquivo = "resultados_metricas.csv"

# Horário de última leitura para evitar processar os mesmos dados repetidamente.
last_horario = None

# loop infinito para ler o arquivo CSV a cada 10 segundos e processar os dados mais recentes.
while True:

    df = pd.read_csv("metricasPandas.csv")

    horas = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # pega a última linha do DataFrame, que contém os dados mais recentes coletados.
    ultimo = df.iloc[-1]

    # Se arquivo não tiver sido atualizado desde a última leitura, espera 10 segundos e continua o loop.
    if ultimo["horario"] == last_horario:
        print(f"sem novos dados desde {horas}")
        time.sleep(10)
        continue

    last_horario = ultimo["horario"]

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

    mediaRam   = round(df["ramUsada"].mean() / 1024**3, 2)
    porcentagemRam = round((ramUsada / ramTotal) * 100, 2)
    mediaDisco = round(df["discoUsado"].mean() / 1024**3, 2)
    porcentagemDisco = round((discoUsado / discoTotal) * 100, 2)

    dados_resultados = {
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
        "mediaRamGB": [mediaRam],
        "mediaDiscoGB": [mediaDisco],
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
Média RAM Usada: {mediaRam} GB

Porcentagem Disco Usado: {porcentagemDisco}%
Média Disco Usado: {mediaDisco} GB

Horário: {horas}
======================================
""")
    
    # Espera 10 segundos antes de realizar a próxima leitura.
    time.sleep(10)


