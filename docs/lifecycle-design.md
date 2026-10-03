# Ciclo de vida de una hipótesis: diseño

Borrador para revisión del Director. **No hay código de esto.** Es el diseño del bucle
que describió Oscar: de un universo se eligen activos, se fijan límites, se hace
discovery, y se repite por calendario o porque la hipótesis vigente dejó de convenir.

---

## 1. Por qué ahora, y qué cambia el resultado de QYLD

El veredicto de la familia `covered-call-equity-index` (sellado) es
**DOES_NOT_GENERALIZE**:

| | Sharpe neto (cota inf.) | R1 | R2 | R4 | R5 | R3 (vínculo del monitor) | control |
|---|---|---|---|---|---|---|---|
| XYLD | 0,660 (+0,200) | pasa | pasa | pasa | pasa | **4 de 6 = 0,667, falla** | SPY 3 de 6 |
| QYLD | 0,677 (+0,222) | pasa | pasa | pasa | pasa | **3 de 7 = 0,429, falla** | QQQ 4 de 7 |

Lo que dice y lo que no:

- Los dos pagan y los dos fallan en **lo mismo**: el monitor. Con SVXY (2 de 5) son tres
  mediciones en las que VIX menos volatilidad realizada no avisa del daño.
- En QYLD el vínculo es peor que el del propio subyacente (3 de 7 contra 4 de 7), así que
  el monitor no detecta la prima. A lo sumo se parece a un detector de caídas.
- **No dice que la prima no exista.** Dice que no sabemos vigilarla con esta variable.
- Consecuencia para el diseño: **hoy no hay ningún miembro autorizado**. Un ciclo de vida
  que arrancara con un banquillo "lleno" estaría describiendo algo que no existe.

## 2. Principios

1. **El estado se deriva, no se guarda.** Cada estado es una consulta sobre registros ya
   sellados (hipótesis, pre-declaración, validación, admisión, veredicto de familia). No hay
   un campo `status` que alguien pueda cambiar sin evidencia.
2. **Disparador significa notificación, nunca capital.** La plataforma avisa y propone; la
   decisión de capital es del Director (filosofía de automatización del proyecto).
3. **El reemplazo sale de un banquillo preparado**, no de una búsqueda hecha después del
   fallo. Buscar bajo presión es como se baja el listón.
4. **El universo se elige por mecanismo y criterios declarados antes de ver retornos.**
   Elegir por rendimiento es minería: 47 hipótesis, 2 validadas contra 6,77 esperadas por azar.
5. **Nada reabre un veredicto sellado.** Volver a medir produce un registro nuevo, con su
   propia fecha y su propio coste de multiplicidad.
6. **La regla solo se endurece sola.** Añadir un miembro a una familia puede hacer el
   veredicto más duro, nunca más fácil; cualquier cosa que lo afloje es del Director.

## 3. Estados de un miembro

Todos se calculan de lo sellado.

| Estado | Condición (derivada) | Quién lo decide |
|---|---|---|
| CANDIDATO | está en el registro de candidatos, sin hipótesis | criterios declarados |
| DECLARADO | hipótesis y pre-declaración selladas, sin datos leídos | plataforma (miembro de familia existente) / Director (familia nueva) |
| MEDIDO | validación de nivel sellada | plataforma |
| DENEGADO | admisión `ADMISION_DENEGADA`; guarda **qué gate** y **qué lo reabriría** | evidencia |
| AUTORIZADO | admisión `ADMITIDA` y `family_clearance` verdadero | evidencia |
| EN_OPERACIÓN | autorizado y con tramo asignado, en PAPER o real | **Director** |
| SUSPENDIDO | monitor degradado (con la confirmación declarada) o disparador de evento | plataforma propone, Director confirma |
| RETIRADO | vida útil vencida, fondo cerrado o veredicto adverso firme | Director |

Hoy: XYLD y QYLD están DENEGADOS por R3, con el mismo motivo de reapertura: *un monitor
con vínculo validado*. SPY: DENEGADO por R3. Crédito: DENEGADO por Sharpe (su R3 pasaba por M2). SVXY: DENEGADO por Sharpe y por R3.
Ninguno AUTORIZADO.

Un estado DENEGADO lleva siempre su **condición de reapertura** escrita (qué evidencia
nueva cambiaría el veredicto). Sin ella no es un estado, es un archivo.

## 4. Disparadores

Cada disparador es una función pura sobre registros sellados que devuelve
`{miembro, qué cambió, opciones}`. Todos terminan en una notificación.

**Calendario**
- Re-medición con datos nuevos. Cadencia propuesta: **anual**. El vínculo del monitor se
  evalúa por folds de ~1,5 años, así que medir con más frecuencia solo añade ruido.
- Revisión de caducidad: cada miembro declara su vida útil (3 años en XYLD); al vencer, se
  notifica antes de que opere sin renovar evidencia.
- Fecha de test hacia adelante ya declarada: **2027-10-06** (nada puede mirarla antes).

**Evento**
- El monitor de un miembro EN_OPERACIÓN pasa a degradado (con la confirmación declarada).
- Cambia el veredicto de una familia (por ejemplo, al sellar uno nuevo).
- La comprobación de distribuciones o de datos anula una medición.
- Un fondo deja de cotizar o cierra (ya ocurrió con PUTW).
- El coste o la capacidad medidos se desvían de lo declarado.

**Lo que NO es un disparador:** un mal resultado de P&L. Distinguir racha mala de
mecanismo muerto por P&L tarda meses, y para entonces la pérdida ya está pagada.

## 5. Banquillo y reemplazo

- Banquillo = miembros AUTORIZADOS sin tramo. Se prepara con antelación.
- Al suspender uno, la plataforma notifica con el candidato a promover y la evidencia
  que lo respalda; promover es del Director.
- **Hoy el banquillo está vacío.** Poblarlo exige monitores con vínculo validado, y eso
  es exactamente lo que falló tres veces.

## 6. Filtro de universo

Criterios declarados antes de mirar retornos, aplicados por la plataforma:

1. Operable sin cortos (el ejecutor solo abre largos).
2. Datos alcanzables y al menos 756 días.
3. Capacidad declarada mayor o igual al tramo.
4. Mecanismo escrito: quién paga, por qué sigue pagando, qué lo termina.
5. Un monitor declarado con variable observable que **no sea P&L**.

Y un **presupuesto de pruebas por periodo**: cada familia o miembro nuevo consume cuota y
se cuenta en el registro de multiplicidad. La cuota existe para que "probar más" no sea
gratis.

## 6 bis. Qué se puede automatizar y qué no

| Automático | Siempre del Director |
|---|---|
| Declarar un miembro nuevo en una familia sellada | Crear una familia nueva |
| Medirlo, juzgar la familia, sellar | Tramo y paso a EN_OPERACIÓN |
| Evaluar disparadores y notificar | Confirmar suspensión y retiro |
| Aplicar el filtro de universo | Cambiar cualquier umbral o regla de gobernanza |

## 7. Decisiones abiertas

1. **Canal de notificación.** No lo he asumido: ¿correo, push, informe diario sellado?
2. **Cadencia anual** de re-medición: ¿de acuerdo, o cada seis meses con la advertencia
   de ruido?
3. **Familia nueva**: ¿siempre del Director, o la plataforma puede proponerla desde el
   registro y esperar visto bueno?
4. **El muro de R3.** El mismo gate frena a todo lo que paga. Hay tres salidas, y la
   elección es del Director: (a) aceptarlo y buscar mecanismos cuyo monitor sí tenga
   vínculo, (b) revisar el §12 con el procedimiento completo (fundamento independiente
   sellado primero, medir lo que admite antes de argumentarlo), (c) un monitor con
   información distinta para este mecanismo, que sería una familia nueva.

## 8. Orden de construcción propuesto

1. **CONSTRUIDO.** `lifecycle_status()`: informe **de solo lectura** que deriva el estado de cada
   miembro de los registros existentes. Sin almacén nuevo, sin cambiar nada sellado.
   Es útil hoy, aunque todos estén denegados.
2. Disparadores como funciones puras con sus tests, sin canal todavía.
3. Notificador, una vez elegido el canal.
4. Filtro de universo y cuota de pruebas.

Lo que no conviene construir antes de la decisión 4: más miembros de esta familia.

---

## 9. Decisiones del Director, 2026-10-03

**1. Canal de notificación.** Correo primero, pensando en que pueda ser una app después.
El notificador se define como una interfaz con una notificación tipada (miembro, qué
cambió, opciones, severidad). El correo es una implementación; una notificación push a
una app sería otra, sin tocar los disparadores. Pendiente de construir; no se ha
escrito código ni se ha pedido ninguna credencial.

**2. Cadencia de re-medición.** Editable, no fija en el código. Vive en
`config/lifecycle.json` (`remeasure_every_months`, 12 por defecto) y la lee
`lifecycle_status()`. Un valor inválido se rechaza, no se sustituye por el de defecto.
**Hecho.**

**3. Quién crea una familia nueva.** Opciones:

| | Qué hace la plataforma | Qué hace el Director | Riesgo |
|---|---|---|---|
| A. Solo el Director | nada, hasta que se lo pidan | escribe y sella cada familia | no escala: es el uno a uno actual |
| B. Propone y el Director aprueba | redacta una propuesta sellada (mecanismo, monitor, miembros, regla) desde el registro | aprueba o rechaza | bajo: la aprobación va antes de medir; cuesta una decisión por familia |
| C. Autónoma dentro de una cuota | crea y mide familias hasta agotar la cuota del periodo, y avisa después | revisa a posteriori | el monitor de una familia es una decisión de gobernanza que se tomaría sin nadie; multiplicidad más difícil de controlar |

Recomendación: **B**. Es la que mantiene el criterio de que el monitor se fija antes de
medir y, a la vez, quita el trabajo de redactar.

**4. Filtro de universo.** Dos cosas distintas que conviene no mezclar:

- **Filtros de operabilidad**, ciegos a los retornos: liquidez (volumen en dólares),
  precio mínimo, historia mínima, spread, que el activo sea largo-operable. Son legítimos
  como filtro de universo y es lo que suelen usar las plataformas cuantitativas.
- **Selección por lo que subió o bajó** el último año: eso es una hipótesis de momentum
  o de reversión, no un filtro. Se declara como hipótesis, con su propio mecanismo y su
  propio coste de multiplicidad. Usarlo para elegir el universo y luego probar
  hipótesis sobre ese universo contaría los retornos dos veces.

Pendiente: revisar cómo lo hacen otras plataformas cuantitativas, y con qué filtros.
