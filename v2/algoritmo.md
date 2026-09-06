# Algoritmo 

```
función procesar_comprobante(documento):

    # 1. Gate
    si NO es_procesable(documento):
        rechazar_o_reencolar(documento)
        retornar

    # 2. Dos flujos en paralelo
    evidencia_vlm = flujo_vlm(documento.imagen)
    evidencia_llm = flujo_llm(documento.ocr_texto)

    # 3. Reglas de programación sobre cada fuente por separado (raw)
    evidencia_vlm.valida = aplicar_reglas_raw(evidencia_vlm)
    evidencia_llm.valida = aplicar_reglas_raw(evidencia_llm)

    # 4. Combinar
    evidencia = combinar(evidencia_vlm, evidencia_llm)   # con fuente + fragmento de sustento por campo

    # 5. Reglas de programación sobre evidencia combinada
    resultado = aplicar_reglas_cruzadas(evidencia)         # negocio + fast-fail + conflicto (R7)

    si resultado.faltan_datos:
        evidencia += buscar_evidencia_adicional(resultado.gaps, max_reintentos=N)  # ej. padrón ARCA
        resultado = aplicar_reglas_cruzadas(evidencia)

    # 6. ¿El código concluyó?
    si resultado.concluye:
        consolidar(resultado, certeza="alta", origen="programa")
        retornar

    # 7. Escalar a agente de IA (solo entre candidatos no descartados)
    decision_agente = agente_ia_decide(
        evidencia=evidencia,
        candidatos_restantes=resultado.candidatos_restantes,   # nunca los ya descartados por código
        reglas_que_fallaron=resultado.conflictos
    )
    consolidar(decision_agente, certeza="baja", origen="agente_ia")

    # 8. HITL — siempre revisa lo de certeza baja, y muestrea lo de certeza alta
    encolar_hitl(decision_agente, prioridad="alta")
    si es_muestra_auditoria(resultado):
        encolar_hitl(resultado, prioridad="baja")   # chequeo periódico de casos "certeza alta"

    # las correcciones del HITL alimentan, con el tiempo:
    #   - ajustes a las reglas de programación
    #   - ejemplos/prompts de flujo_vlm y flujo_llm
```
