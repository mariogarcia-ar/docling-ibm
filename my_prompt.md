python classification_pipeline.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md' 
python extraction_pipeline.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md' 
python full_pipeline.py 'files/2025-08/2D2C9343/2991f57d-c143-4b23-9f87-4dfb1214ef53.md' 


python full_pipeline.py 'files/2025-08/' 
python classification_pipeline.py 'files/2025-08/' 
python extraction_pipeline.py 'files/2025-08/' 

find ./files -type f -name "*.json" -exec rm {} +


ollama ps 

curl http://localhost:11434/api/generate -d '{
  "model": "qwen2.5vl:3b",
  "prompt": "Explica la teoría de la relatividad en una frase corta",
  "stream": false
}'