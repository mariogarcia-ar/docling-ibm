# Flujo 

- preparar los archivos
  - convertir pdf a imagenes : voucherflow pdf
  - reducir imagenes grandes : voucherflow corpus
- invocar llm frontier (deepseek):  voucherflow-lab
- invocar llm local 
- evaluar y corregir
- publicar




Capacidad	Pregunta que responde	Módulo
- processing	    ¿Cómo se lee este archivo?	      processing/
- validation	    ¿Esto es un comprobante?	        validation/
- classification	¿Qué tipo es y cómo se imputa?	  classification/
- extraction	    ¿Qué dice el documento?	          extraction/
- conclusion	    ¿Cuál es el veredicto?	          conclusion/

# cmd 


1 - corpus.md (reducir las imagenes)
3 - pdf.md (exportar el pdf a imagenes)
2 - llm.md (invocar a deepseek para extraer informacion)



voucherflow corpus  tests/fixtures -o var/fixtures --workers 4
voucherflow pdf     tests/fixtures -o var/fixtures 
voucherflow process var/fixtures -o var/fixtures-extracted


voucherflow corpus var/files -o var/processed --workers 4
voucherflow pdf var/files -o var/processed
voucherflow process var/files -o var/files-extracted  # (ocr extraction - image/pdf)
voucherflow validate 






 voucherflow-lab tests/fixtures/expected-extraction -M extraer -p deepseek --workers 4 -o tests/expected-extraction --dry-run\n
 
 voucherflow-lab tests/fixtures/expected-extraction -M extraer -p deepseek --workers 4 -o tests/ixtures-extraction/expected-extraction 


voucherflow pdf tests/fixtures/chicos -o var/paginas
voucherflow pdf tests/fixtures -o var/paginas



 voucherflow-lab tests/fixtures/chicos -M extraer -p deepseek --workers 4 -o tests/fixtures-extraction/chicos


python scripts/operacion/pdf-a-imagen.py tests/fixtures/chicos --dry-run


# refactorizacion
no hacer sobre ingenieria 
- procesar con workers y con stop 
- cada accion deberia poder aplicarse a un archivo o carpeta
- hay que hacer el flujo primario y luego meter todo lo que es ingenieria


tengo varios archivos (imagenes, pdf, etc)
el pdf si es con texto nos conviene simplificar con pdftotext o similar
si el pdf es solo imagen conviene exportarla a imagen 
las imagenes luego las redimensionamos (pero lo mas importante es el dpi)




# revisar
- es nacional o internacional

- comprimir las imagenes
- revisar la calidad y que procesamiento tengo que realizar en los otros docs o archivos.
- crear un lote usando llm frontier (el lote tiene como objetivo tenerlo como referencia para un posterior entrenamiento)
- proceso de extraccion y validacion
 - extraer ocr de pdf
 - extraer ocr de imagenes
 - extraer ocr de otros formatos

 - extrer informaicon de las facturas, nd, nc
  - usando ocr
  - usando vision

- validar informacion extraida de facturas, nd, nc
  - usando reglas de negocios (programacion)
  - usando reglas de llm 
  - usando reglas de vision

- concluir
  - 



# Flujo
 - es procesable por texto, continuar

 - es procesable por imagen, continuar
 - ocr (raw, boxes)
 - extraccion (datos relevantes por texto / imagen) hay reglas de extraccion qeu son mejores por visual ej Tipo Factura viene en rectangulo en letra mayuscula
 - validacion (evidencia de la extracion  y reglas de programacion aplicadas a la extraccion, ej si hay 2 cuit entonces es Factura A)
 - consolidacion : con la evidencia y validacion , sacar una conclusion


# notes
read README.md
keep this simplicity for the pseudocode 
tidy README.md 


brew install libmagic
 pip install python-magic


# comandos
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


python document_extraction.py 'files/2026-07/0208340E/167fe1c3-be8a-4864-ad1f-ec045bebd7df.jpg'  -M 11.1


tenemos un problema de temperatura del dispositivo en el cual luego de 10 min de procesamiento, deberiamos para 2 minutos los procesos esperando que la maquina se enfrie 

el tema esta en que los 2 minutos de enfriamiento deberian empezar a contar cuando todos los workers estan detenidos , es decir a los 10 min desde la ultima parada, empezar 

