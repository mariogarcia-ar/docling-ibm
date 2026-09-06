# Flujo de detección y validación de tipo de comprobante

## Idea central

Dado un comprobante, se ejecutan **dos flujos especializados en paralelo** (uno de
visión, uno de razonamiento sobre texto). La evidencia de ambos se combina y se
somete a **reglas de programación determinísticas**. Solo cuando el código no
puede concluir con certeza, el caso escala a un **agente de IA** que toma una
decisión. En todos los casos, **el HITL (revisión humana) tiene la última
palabra** y es la fuente que corrige y madura el sistema con el tiempo.

La certeza de una conclusión no se basa en que un modelo "se sienta seguro"
(los LLM calibran mal su propia confianza). Se basa en **qué etapa del flujo
resolvió el caso**: si lo resolvió el código, la certeza es alta; si tuvo que
intervenir un agente de IA, la certeza es baja y el caso queda marcado para
revisión.

---

## Etapas del flujo

### 1. Gate de procesabilidad
Antes de gastar OCR o llamadas a modelo, se valida que el documento sea
procesable como imagen: calidad, formato, resolución mínima. Si no pasa, se
rechaza o se reencola — no entra al pipeline principal.

### 2. Dos flujos especializados en paralelo

- **Flujo VLM**: lee la imagen directamente. Prioriza lo que ve (ej. la letra
  grande dentro del recuadro del encabezado) sobre cualquier inferencia de
  texto.
- **Flujo LLM (razonamiento)**: lee el OCR/Markdown y aplica razonamiento
  sobre el texto extraído.

Ambos corren siempre, no se elige uno u otro por documento. Cada uno devuelve
su evidencia con el mismo esquema: valor extraído, campo, y de dónde salió
(fuente + fragmento que lo sustenta), para que después se puedan comparar
programáticamente.

### 3. Reglas de programación — primera pasada (sobre evidencia raw)

Se valida cada fuente **por separado**, antes de mezclarlas: ¿la extracción
del VLM es internamente consistente con las reglas de negocio? ¿la del LLM
también? Una fuente que no pasa su propio chequeo (ej. dice "Factura A" pero
no detectó los dos CUIT que esa letra exige) queda marcada como debilitada
antes de compararse con la otra.

### 4. Evidencia combinada

Se junta lo que sobrevivió del paso anterior en un solo objeto de evidencia,
con trazabilidad de qué fuente aportó cada dato.

### 5. Reglas de programación — segunda pasada (sobre evidencia procesada)

Acá corren las reglas de cruce entre fuentes: negocio tributario, fast-fail
por letra, y las reglas de conflicto (ej. Responsable Inscripto emisor +
receptor Responsable Inscripto + documento detectado como "B" → alerta).
Si hace falta, se busca evidencia adicional puntual (ej. consulta al padrón
ARCA) con un objetivo concreto y un límite de reintentos — esto no es un loop
abierto de "seguir buscando hasta encontrar algo".

### 6. ¿Las reglas concluyen?

- **Sí** → el caso se consolida con **certeza alta** (post-programa). No pasó
  por un agente de IA para decidir; solo se validó con lógica determinística.
- **No** → escala al agente de IA.

### 7. Agente de IA decide (solo si el código no pudo)

El agente recibe la evidencia de ambas fuentes, las reglas que se dispararon
y las que no pudieron resolver el conflicto. **Importante**: solo puede
elegir entre los candidatos que sobrevivieron a las reglas de descarte — no
puede resucitar una letra que el código ya eliminó con certeza (ej. si el
padrón confirma Responsable Inscripto, "C" no es una opción válida aunque el
agente lo considere).

El resultado de esta etapa se consolida con **certeza baja** (post-agente).

### 8. HITL — autoridad final

Todo caso de certeza baja (resuelto por el agente de IA) llega a revisión
humana. El HITL no es solo una bandeja de salida: es la fuente de verdad que:

- **Corrige** las decisiones del agente de IA cuando están mal.
- Con el tiempo, esas correcciones son la señal para **ajustar las reglas de
  programación** (mover casuística que hoy resuelve el agente hacia reglas
  determinísticas más maduras) y para **mejorar los prompts/ejemplos** de los
  dos flujos especializados.
- Es también donde se detecta cuándo apareció un formato de comprobante nuevo
  que ninguno de los dos modelos maneja bien (señal: sube la tasa de
  desacuerdo entre VLM y LLM).

A medida que la solución madura en la empresa, el objetivo es que la
proporción de casos que llegan a "certeza alta por programa" crezca y la
proporción que depende del agente de IA (y por lo tanto de HITL) baje — no
porque se relaje el criterio, sino porque la casuística real observada se va
codificando como regla.

---

## Decisiones que quedan abiertas para definir antes de implementar

- **Contrato de evidencia**: mismo esquema de campos entre VLM y LLM (fuente,
  fragmento de sustento, valor) para que las reglas puedan comparar
  programáticamente.
- **Tabla de precedencia por campo**: qué fuente gana en cada tipo de
  desacuerdo (ej. visual gana en la letra del encabezado si el recuadro se
  detectó con claridad).
- **Alcance de "buscar más evidencia"**: qué gatilla una consulta adicional
  (ej. padrón ARCA) y cuántos reintentos como máximo antes de escalar al
  agente.
- **Auditoría de "certeza alta"**: muestreo periódico de casos resueltos por
  programa, porque una regla puede matchear por accidente en un caso real que
  no se previó (ej. agente/comisionista con más de dos CUIT) y consolidarse
  como correcto sin haber pasado nunca por HITL.
- **Trazabilidad completa**: versión de prompt, modelo usado, evidencia de
  cada fuente, regla disparada y quién (código o agente) tomó la decisión
  final — necesario para poder justificar una clasificación fiscal si alguna
  vez se audita.
