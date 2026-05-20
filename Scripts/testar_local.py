import os
import sys
import pandas as pd
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import leitura

os.environ["MYSQL_HOST"] = "127.0.0.1"
os.environ["MYSQL_USER"] = "root"
os.environ["MYSQL_PASSWORD"] = "Corinthians"
os.environ["MYSQL_DATABASE"] = "magnes"

class LocalS3Mock:
    def get_object(self, Bucket, Key):
        print(f"[Local Test] Lendo o arquivo 'dadosBrutos.csv' gerado pelo seu script de escrita...")
        return {"Body": open("dadosBrutos.csv", "r", encoding="utf-8")}

    def put_object(self, Bucket, Key, Body, ContentType):
        filename = Key.split("/")[-1]
        print(f"[Local Test] Salvando resultado final do processamento em: {filename}")
        mode = "wb" if isinstance(Body, bytes) else "w"
        with open(filename, mode) as f:
            f.write(Body)

leitura.s3 = LocalS3Mock()

if __name__ == "__main__":
    print("=" * 60)
    print("RODANDO PIPELINE COMPLETO 100% LOCAL")
    print("=" * 60)
    
    try:
        resultado = leitura.lambda_handler(event={}, context=None)
        
        print("\n" + "=" * 60)
        print("SUCESSO: Dados capturados, processados com regras financeiras e salvos!")
        print("=" * 60)
        
        with open("dadosPerfeitos.json", "r", encoding="utf-8") as f:
            dados_finais = json.load(f)
            print(json.dumps(dados_finais, indent=2, ensure_ascii=False))
            
    except Exception as e:
        print(f"\nErro no teste local: {str(e)}")