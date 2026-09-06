git archive --format=zip -o cvc_local_llm.zip HEAD


python classification_pipeline.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md' 
python extraction_pipeline.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md' 
python full_pipeline.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md' 


python full_pipeline.py 'files/2025-08/' 
python classification_pipeline.py 'files/2025-08/' 
python extraction_pipeline.py 'files/2025-08/' 

find ./files -type f -name "*.json" -exec rm {} +

git archive --format=zip -o cvc_local_llm.zip HEAD
find ./files -type f -name "*.md" | zip -@ files_md.zip
find ./files -type f -name "*.json" | zip -@ files_json.zip



ollama ps 

curl http://localhost:11434/api/generate -d '{
  "model": "qwen2.5vl:3b",
  "prompt": "Explica la teoría de la relatividad en una frase corta",
  "stream": false
}'

files/2026-07/0208340E/167fe1c3-be8a-4864-ad1f-ec045bebd7df.jpg
files/2026-07/0208340E/167fe1c3-be8a-4864-ad1f-ec045bebd7df.raw.md

python extraction_pipeline.py 'files/2026-07/0208340E/167fe1c3-be8a-4864-ad1f-ec045bebd7df.raw.md' 


python ask.py 'files/2026-07/0208340E/167fe1c3-be8a-4864-ad1f-ec045bebd7df.raw.md'  -q "¿Cuál es el importe total?"
python ask.py 'files/2026-07/0208340E/167fe1c3-be8a-4864-ad1f-ec045bebd7df.jpg'  -q "¿Cuál es el importe total?"

python ask.py 'files/2026-07/0208340E/167fe1c3-be8a-4864-ad1f-ec045bebd7df.jpg'  -q "Es una factura del tipo A, B o C? Tener en cuenta que el tipo aparece como una sola letra en mayuscula cerca del termino factura."



tenemos un problema de temperatura del dispositivo en el cual luego de 10 min de procesamiento, deberiamos para 2 minutos los procesos esperando que la maquina se enfrie 

el tema esta en que los 2 minutos de enfriamiento deberian empezar a contar cuando todos los workers estan detenidos , es decir a los 10 min desde la ultima parada, empezar 

