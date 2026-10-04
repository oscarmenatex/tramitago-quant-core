# Cribado de monitores: primer resultado (2026-10-03)

Espacio sellado `MONITOR_SCREEN_SPACE|875e6329…`, escaneo sellado `MONITOR_SCREEN_SCAN|9afe9691…`.
Ejecutado por el Director en su máquina local (la de evidencia); la VM solo sirvió para FRED. **El
holdout no se ha abierto.**

---

## 1. Qué salió

| | Candidatos |
|---|---|
| Alcanzable y **refutado** | 10 |
| **Alcanzable y cumplido** | 1 (C14) |
| **Inalcanzable** | 3 (C01, C02, C10: inversión de la curva) |

Por azar se esperaban **3,2** aprobados falsos de 14. Salió uno.

## 2. Lectura de cada grupo

**Los tres inalcanzables son la inversión de la curva.** Solo uno de los siete folds (T10Y2Y) o tres (T10Y3M)
llegaron a diez días de estado activo, por debajo de los cinco que exige la regla. Es el problema del §12 de DOC-011 otra vez: el
monitor económicamente más natural para una recesión es el que no se puede probar con esta resolución.

**El único aprobado (C14: dólar fuerte → SVXY) no es un hallazgo.**
- Pasa con 5 de 7 folds, el mínimo que permite la regla (0,714 contra 0,70).
- Su magnitud es muy desigual: −93 y −40 puntos básicos por día en dos folds, frente a −11, −34 y −25 en
  los otros tres que cumplen. Los dos que fallan son pequeños (+8 y +9). Un solo fold domina la media.
- Su mecanismo es el más débil del espacio (el dólar como canal de estrés de financiación), mientras que
  el candidato con el mecanismo más directo para la misma exposición, la subida del VIX (C12), sacó
  **2 de 7**. Que pase el candidato con peor razón y falle el que tiene la mejor es lo que cabe esperar
  del azar.
- Un dólar que sube durante 21 días coincide con episodios de estrés de mercado, así que lo más probable
  es que sea un sustituto de que la volatilidad se agrupa, no una señal propia.

**Los diez refutados** incluyen un patrón que conviene describir sin sobreinterpretarlo. Los tres que usan
el spread de crédito Baa (C03, C08, C13) sacan 2 de 7, y el que lo compara con su mediana (C04) saca
**0 de 6**, con diferencias de signo contrario al declarado en todos los folds usables. Es decir, cuando el
spread está alto, los retornos del día siguiente tienden a ser *mayores*, no menores.
- **No se puede darle la vuelta.** La dirección está declarada una sola vez y no se busca en ambos sentidos;
  invertirla ahora sería elegir el signo que funcionó, que es justo lo que la regla prohíbe.
- Si se quisiera explorarlo, sería una hipótesis **nueva** (la prima de riesgo es mayor cuando el spread es
  alto), con su propia declaración y su propia multiplicidad, no un monitor.

## 3. Qué dice sobre el bloqueo

Sumando los cuatro vínculos medidos antes (tres refutados, uno inalcanzable) y este cribado, **no hay ningún
monitor con un vínculo creíble** entre las variables y exposiciones ensayadas. Con la salvedad de potencia:
siete folds de un año tienen poca, así que un vínculo real pero modesto podría no verse. Lo que el resultado
sostiene es que, con este catálogo y esta resolución, **monitores del tipo M1 para estas exposiciones no
aparecen**, no que no existan.

## 4. Sobre abrir el holdout (decisión pendiente del Director)

El holdout se abre una sola vez, y solo para C14.

| | Abrir | No abrir |
|---|---|---|
| Qué se aprende | Si un monitor basado en el dólar anticipa a SVXY en 2023–2026 | Nada nuevo |
| Qué desbloquea | **Nada**: bajo la regla C es un requisito adicional, y SVXY ya falla el Sharpe (cota inferior −0,0445), así que no podría admitirse aunque confirmara | Nada se pierde |
| Coste | Gasta el holdout, y esos tres años dejan de ser vírgenes para cualquier cribado futuro | Ninguno |
| Probabilidad de que sea real | Baja: un aprobado de 14 con 3,2 esperados por azar y sin mecanismo fuerte | |

**Recomendación: no abrirlo.** Es una recomendación, no una decisión mía, y se puede revertir a cambio de
nada porque `confirm` no ha corrido.

## 5. Lo que queda por decidir

Que el cribado no encuentre monitores no cierra el trabajo, pero cambia dónde está el bloqueo. Las salidas
que quedan están en manos del Director y se pueden medir antes de argumentarlas:

1. Revisar el §12 / M2.x con el procedimiento completo.
2. Aceptar que, con el catálogo actual, la combinación (Sharpe neto de 0,5 **y** monitor validado) puede
   estar fuera de lo que ofrecen primas accesibles a este tamaño, y plantear qué significa entonces la Fase 1.
3. Ampliar el catálogo solo hacia familias económicas investigables y justificadas por el siguiente
   entregable (decisión 4), por ejemplo monitores de tipo identidad para primas de carry.
