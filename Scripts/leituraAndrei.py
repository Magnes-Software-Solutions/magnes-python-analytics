import datetime
import time
import pandas as pd
import os
import mysql.connector
import random
import json

# ==========================================
# BUCKET LOCAL
# ==========================================

bucket_local = r"C:\Users\andre\OneDrive\Desktop\BucketLocal"

os.makedirs(
    rf"{bucket_local}\trusted",
    exist_ok=True
)

os.makedirs(
    rf"{bucket_local}\client",
    exist_ok=True
)

arquivo_raw = rf"{bucket_local}\raw\dadosBrutos.csv"

arquivo_trusted = rf"{bucket_local}\trusted\dadosTratados.csv"

arquivo_client = rf"{bucket_local}\client\dadosPerfeitos.json"

# ==========================================
# CONTROLE DE LEITURA
# ==========================================

last_index = 0
last_index_trusted = 0

# ==========================================
# MYSQL
# ==========================================

conn = mysql.connector.connect(
    host="localhost",
    user="gerente",
    password="Teste123",
    database="magnes"
)

cursor = conn.cursor()

#-----------------------------------------------------ANDREI----------------------------------------------------------------------------------------#
# ======================================
# NOVOS DADOS (CLIENT)
# ======================================

def gerar_corrente():
    # 60% dentro da faixa normal
    if random.random() <= 0.6:
        return random.randint(115, 135)
    # 40% fora
    return random.choice([
        random.randint(90, 114),
        random.randint(136, 160)
    ])

def gerar_poeira():
    return random.randint(0, 100)

#-----------------------------------------------------ANDREI----------------------------------------------------------------------------------------#
# ==========================================
# LOOP
# ==========================================

while True:

    # ==========================================
    # LEITURA RAW
    # ==========================================

    if not os.path.exists(arquivo_raw):

        print("Arquivo raw ainda não existe")

        time.sleep(10)

        continue

    df = pd.read_csv(arquivo_raw)

    novos = df.iloc[last_index:]

    if novos.empty:

        print("Sem novos dados")

        time.sleep(10)

        continue

    # ==========================================
    # TRATAMENTO TRUSTED
    # ==========================================

    for _, ultimo in novos.iterrows():

        horas = datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

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

#-----------------------------------------------------ANDREI----------------------------------------------------------------------------------------#
        cpuBase = ultimo["cpuPorcentagem"]

        # 60% normal / 40% estressado
        if random.random() < 0.4:
            cpuPorcentagem = min(cpuBase * 7.5, 100)
        else:
            cpuPorcentagem = cpuBase

#-----------------------------------------------------ANDREI----------------------------------------------------------------------------------------#

        cpuNucleosFisicos = ultimo["cpuNucleosFisicos"]

        cpuNucleosLogicos = ultimo["cpuNucleosLogicos"]

        total_processos = ultimo["totalProcessos"]

        cpuTempoUser = round(
            ultimo["cpuTempoUser"] / 60
        )

        cpuTempoSistema = ultimo["cpuTempoSistema"]

        cpuTempoInativo = ultimo["cpuTempoInativo"]

        ramLivre = round(
            ultimo["ramLivre"] / 1024**3,
            2
        )

        ramUsada = round(
            ultimo["ramUsada"] / 1024**3,
            2
        )

        ramTotal = round(
            ultimo["ramTotal"] / 1024**3,
            2
        )

        discoLivre = round(
            ultimo["discoLivre"] / 1024**3,
            2
        )

        discoUsado = round(
            ultimo["discoUsado"] / 1024**3,
            2
        )

        discoTotal = round(
            ultimo["discoTotal"] / 1024**3,
            2
        )

        porcentagemRam = round(
            (ramUsada / ramTotal) * 100,
            2
        )

        porcentagemDisco = round(
            (discoUsado / discoTotal) * 100,
            2
        )

        dados_resultados = {

            "empresa":[empresa],

            "macAddress":[macAddress],

            "horas":[horas],

            "cpuPorcentagem":[cpuPorcentagem],

            "cpuNucleosFisicos":[cpuNucleosFisicos],

            "cpuNucleosLogicos":[cpuNucleosLogicos],

            "cpuTempoUser":[cpuTempoUser],

            "cpuTempoSistema":[cpuTempoSistema],

            "cpuTempoInativo":[cpuTempoInativo],

            "ramLivre":[ramLivre],

            "ramUsada":[ramUsada],

            "ramTotal":[ramTotal],

            "discoLivre":[discoLivre],

            "discoUsado":[discoUsado],

            "discoTotal":[discoTotal],

            "porcentagemRam":[porcentagemRam],

            "porcentagemDisco":[porcentagemDisco],

            "totalProcessos":[total_processos]
        }

        df_resultados = pd.DataFrame(
            dados_resultados
        )

        if not os.path.exists(arquivo_trusted):

            df_resultados.to_csv(
                arquivo_trusted,
                index=False
            )

        else:

            df_resultados.to_csv(
                arquivo_trusted,
                mode="a",
                header=False,
                index=False
            )

        print(f"""
======================================
EMPRESA: {empresa}

MAC: {macAddress}

--------------------------------------

CPU: {cpuPorcentagem}%

RAM: {porcentagemRam}%

DISCO: {porcentagemDisco}%

--------------------------------------

RAM USADA:
{ramUsada} GB

DISCO USADO:
{discoUsado} GB

--------------------------------------

PROCESSOS:
{total_processos}

HORÁRIO:
{horas}

======================================
""")

    # ==========================================
    # LEITURA TRUSTED
    # ==========================================

    df_trusted = pd.read_csv(
        arquivo_trusted,
        on_bad_lines="skip"
    )

    novos_trusted = df_trusted.iloc[
        last_index_trusted:
    ]

    # ==========================================
    # TRATAMENTO CLIENT
    # ==========================================

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
                MAX(
                    CASE
                    WHEN c.tipoComponente = 'Processador'
                    THEN cm.limite
                    END
                ) as limiteCPU,

                MAX(
                    CASE
                    WHEN c.tipoComponente = 'Memória'
                    THEN cm.limite
                    END
                ) as limiteRAM,

                MAX(
                    CASE
                    WHEN c.tipoComponente = 'Armazenamento'
                    THEN cm.limite
                    END
                ) as limiteDisco

                FROM componente_maquina cm

                JOIN componente c
                ON cm.fkComponente = c.idComponente

                WHERE cm.fkMaquina = %s
            """, (macAddress,))

            limites = cursor.fetchone()

            limiteCPU = limites[0] if limites else None

            limiteRAM = limites[1] if limites else None

            limiteDisco = limites[2] if limites else None

            alertaCPU = False
            alertaRAM = False
            alertaDisco = False

            if limiteCPU and cpuPorcentagem > limiteCPU:
                alertaCPU = True

            if limiteRAM and porcentagemRam > limiteRAM:
                alertaRAM = True

            if limiteDisco and porcentagemDisco > limiteDisco:
                alertaDisco = True

            # ======================================
            # NOVOS DADOS (CLIENT INTELIGENTE)
            # ======================================

#-----------------------------------------------------ANDREI----------------------------------------------------------------------------------------#

            correnteEletrica = gerar_corrente()
            poeiraCapturada = gerar_poeira()

            dados_client = {

                "empresa": empresa,
                "macAddress": macAddress,
                "horario": horas,

                "cpuUso": cpuPorcentagem,
                "limiteCPU": limiteCPU,
                "alertaCPU": alertaCPU,

                "correnteEletrica": correnteEletrica,
                "poeiraCapturada": poeiraCapturada
            }
#-----------------------------------------------------ANDREI----------------------------------------------------------------------------------------#
            # ==========================================
            # SALVAR CLIENT EM JSON
            # ==========================================

            registro_json = dados_client

            arquivo_temp = arquivo_client + ".tmp"

            with open(arquivo_temp, "w", encoding="utf-8") as f:

                json.dump(
                    [registro_json],
                    f,
                    ensure_ascii=False,
                    indent=4
                )

            os.replace(arquivo_temp, arquivo_client)

            print(f"""
======================================
EMPRESA: {empresa}
MAC: {macAddress}
HORÁRIO: {horas}

==================== SISTEMA ====================

CPU: {cpuPorcentagem}%     | ALERTA: {alertaCPU}
RAM: {porcentagemRam}%     | ALERTA: {alertaRAM}
DISCO: {porcentagemDisco}% | ALERTA: {alertaDisco}

==================== AMBIENTE ===================

CORRENTE: {correnteEletrica}V
POEIRA: {poeiraCapturada}

==================================================
TOTAL PROCESSOS: {total_processos}
======================================
""")

    # ==========================================
    # ATUALIZA ÍNDICES
    # ==========================================

    last_index = len(df)

    last_index_trusted = len(df_trusted)

    # ==========================================
    # INTERVALO
    # ==========================================

    time.sleep(10)