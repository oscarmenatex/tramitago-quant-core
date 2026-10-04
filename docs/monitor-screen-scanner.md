# Escáner del cribado de monitores

Módulo aparte (decisión 2 del Director): `tramitago_quant_core/research/monitor_screen.py`, con su capa
de lectura en `monitor_screen_io.py` y su CLI en `scripts/research/run_monitor_screen.py`. **Está
construido y probado con datos sintéticos; no se ha ejecutado.** Discovery no se tocó.

---

## 1. Cómo se ejecuta: tres pasos, y el orden es el contrato

```powershell
python3.11 -B scripts\research\run_monitor_screen.py seal
```
Valida el espacio y lo sella. **No lee ninguna serie ni ningún retorno.** Imprime la identidad sellada.
Ejecútalo antes de nada: el escaneo se niega a correr sobre un espacio que no esté sellado tal cual.

```powershell
python3.11 -B scripts\research\run_monitor_screen.py scan
```
**Lo ejecutas tú** (necesita la VM para FRED; no necesita credenciales de Alpaca). Descarga seis series,
lee los retornos de los tres datasets sellados **solo de la ventana de descubrimiento**, observa los 14
candidatos, los sella todos con la multiplicidad y los imprime. Un aprobado aquí no es evidencia.

```powershell
python3.11 -B scripts\research\run_monitor_screen.py confirm --spend-the-holdout
```
Abre el holdout, **una sola vez**. No hace nada si ningún candidato pasó, y entonces el holdout sigue sin
abrir. La bandera existe para que no se ejecute por accidente.

## 2. Qué garantiza

| Garantía | Cómo |
|---|---|
| El espacio no cambia tras ver un resultado | Se sella antes; el escaneo compara el espacio con el sellado y se niega si difiere en lo más mínimo o si el registro sellado no reproduce su identidad |
| El escaneo no puede leer el holdout | Solo recibe filas de la ventana de descubrimiento y **lanza** ante una fila fuera de ella; además el cargador recorta la ventana al leer, así que el holdout ni siquiera está en memoria |
| Es tan duro como el gate que enfrentará una prima | Usa la regla M1 del motor (`LINK_*`), no una copia |
| Ningún estado mira al futuro | Usa solo observaciones hasta el día (o hasta el día anterior si la serie se publica con retraso); la mediana excluye el día que compara; probado con un test de propiedad |
| Un día sin dato no cuenta como disparo | Es desconocido, marcado 0 y contado aparte |
| La dirección no se busca en ambos sentidos | El signo opuesto plantado sale refutado, no hallado |
| El holdout se gasta una vez | Se sella un marcador **antes** de leer los retornos. Si algo falla después, el intento sigue contando: no se puede reintentar |
| La corrección por múltiples candidatos | La confianza del holdout es 1 − 0,05/*k*, con *k* el número que pasó (regla sellada antes del escaneo) |
| Todo lo examinado queda registrado | Se sellan los 14 candidatos, no solo los que pasan, con cuántos se esperan por azar |

## 3. Lo que no está hecho

- **No se ha ejecutado**: ninguna serie ni retorno se ha leído. El resultado es desconocido.
- **La aplicación de la regla C no está en el código de admisión.** Un vínculo confirmado es un requisito
  adicional, pero la puerta de monitorabilidad aún no lo exige: se cablea cuando haya algo que cablear,
  como un esquema de admisión **nuevo** (los esquemas sellados 1 a 4 no se editan).
- **Los 14 candidatos y las seis variables están fijados** (decisión 3 del Director) hasta que haga falta
  cambiarlos para desbloquear algo.

## 4. Cómo leer el resultado

- Con 7 folds y 14 candidatos se esperan unos **3,2 aprobados falsos** por azar. Que algo pase en
  descubrimiento es lo normal, no una noticia.
- Si **nada** pasa el descubrimiento, la conclusión limpia es que estas variables no anticipan los retornos
  de estas exposiciones con la resolución disponible, y el holdout queda sin abrir.
- Si algo confirma en el holdout, sigue siendo **un requisito adicional**: la prima que se apoye en ello debe
  además pasar M1 propio y superar un Sharpe neto de 0,5. Hoy ninguna cumple ambas cosas.
