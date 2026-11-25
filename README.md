# Portal de bandas de regresión (EE. UU. > 50.000 M$)

Este portal automatiza la estrategia del vídeo *Stock Investing Strategy*: un watchlist de empresas grandes, el indicador *Standard Deviation Channel* para saber cuándo comprar y un filtro fundamental para saber cuándo no comprar. Además lleva el seguimiento de tus posiciones hasta que tocan la recta de regresión.

Cada día laborable, después del cierre de Wall Street, GitHub ejecuta el proceso y publica la web.

## Reglas que aplica

| Paso | Regla |
|---|---|
| Universo | Acciones del mercado de EE. UU. con capitalización > 50.000 M$. Cada empresa aparece una sola vez, como el filtro *Primary Listing* de TradingView: GOOG y GOOGL, o BRK-A y BRK-B, se agrupan por el CIK de la SEC. |
| Indicador | *Standard Deviation Channel* sobre el gráfico diario. **Length = 2500**, bandas superiores +1 y +2, inferiores −3 y −4. Usa el cierre ajustado por splits pero no por dividendos, igual que TradingView. |
| Se descarta | Menos de 2500 sesiones de historia, canal plano o bajista, o la banda −3σ o −4σ por debajo de 0. |
| Compra | Top 10 por capitalización: en −3σ o por debajo. Resto de empresas: en −4σ o por debajo. Las que están entre −3σ y −4σ aparecen como **Vigilar**. |
| Antes de comprar | Dos preguntas para tu IA: ¿la caída es temporal? ¿hay riesgo de quiebra? El botón «Copiar análisis para mi IA» prepara el prompt con los datos. |
| Salida | Cuando el precio toca la recta de regresión. Se puede tomar el beneficio entero o una parte. Sin stop loss. |
| Seguimiento | Repetir el análisis fundamental cada 3 meses. Solo se vende con pérdida si cambia la respuesta a alguna de las dos preguntas. |
| Riesgo | Entre el 5 % y el 10 % de la cartera por empresa (de 10 a 20 empresas). Si no hay señales, se mantiene liquidez. |

`tests/test_indicator.py` comprueba que el cálculo coincide con el bucle del script de Pine. También comprueba el canal móvil, que es lo que verías con el replay de TradingView en cada fecha.

## Pestañas de la web

- **Señales**: empresas en compra o en vigilancia, con su riesgo de quiebra y su gráfico.
- **Watchlist**: tus posiciones. Muestra para cada una:
  - resultado desde la entrada y σ actual;
  - distancia a la recta y si ya la ha tocado (🎯, con la fecha);
  - revisión trimestral pendiente;
  - peso de cada posición y liquidez total, con avisos si te sales de la gestión de riesgo.

  Desde el menú ⋯ puedes copiar el prompt de revisión, marcar la posición como revisada, cerrarla (vender), editarla o eliminarla.
- **Todas**: todo el universo ordenado por desviación.
- **Descartadas**: las que no cumplen las reglas y el motivo.

Para añadir una posición, usa la estrella ☆ de la tabla, el botón de la ficha de la empresa o «+ Añadir posición».

## Veredicto de la IA en 1–2 frases

Los prompts piden a tu IA que termine cada empresa con un bloque fijo:

```
[[VEREDICTO]]
TICKER: MCD
CAIDA: TEMPORAL | PERMANENTE | DUDOSA
QUIEBRA: BAJO | MEDIO | ALTO
LUZ_VERDE: SI | NO          (en la revisión: CAMBIO y MANTENER)
MOTIVO: …
[[FIN]]
```

Pega la respuesta completa en la ficha de la empresa («Veredicto de tu IA») o con el botón «Pegar respuesta de la IA». El portal la cruza con el indicador y te resume la decisión:

- ✅ **Comprar**: en zona de compra y con luz verde.
- ⚠️ **Comprar con cautela**: en zona de compra, pero la IA tiene dudas; mejor una posición pequeña.
- ⏳ **Esperar a −4σ** o **Sin señal**: los fundamentales están bien, pero el precio aún no está en zona de compra.
- ❌ **No comprar**: caída permanente, riesgo de quiebra o sin luz verde.

En las posiciones abiertas, el resumen es 🟢 **Mantener** o 🔴 **Vender**. Al guardar el veredicto, la posición queda marcada como revisada.

Los veredictos se guardan en `watchlist.json` (sincronizados) y aparecen como icono en la columna **IA**. Si el icono sale atenuado, el veredicto tiene más de 3 meses.

## Puesta en marcha (una sola vez)

1. Crea un repositorio en GitHub (por ejemplo `stocks-regresion-strategy`). En el plan gratuito, GitHub Pages solo funciona con repositorios **públicos**. Lee la nota de privacidad más abajo.
2. Sube todo el contenido de esta carpeta, **incluida la carpeta oculta `.github`**:
   ```bash
   git init && git add . && git commit -m "Portal bandas" && git branch -M main
   git remote add origin https://github.com/alex-j10/stocks-regresion-strategy.git && git push -u origin main
   ```
3. En el repositorio, ve a **Settings → Pages → Source** y elige **GitHub Actions**.
4. Ve a **Actions → Actualizar portal → Run workflow**. La primera ejecución tarda entre 10 y 20 minutos.
5. Recomendado: en **Settings → Secrets and variables → Actions**, crea el secreto `SEC_USER_AGENT` con el valor `Tu Nombre tu@email.com`.

### Subdominio propio

1. En el DNS de tu dominio, crea un registro **CNAME**: nombre `bandas`, valor `TU_USUARIO.github.io`.
2. En **Settings → Pages → Custom domain**, escribe `bandas.tudominio.com`. Cuando el DNS propague, marca **Enforce HTTPS**.
3. Pon el mismo dominio en `config.json` → `"custom_domain"`.

### Sincronizar el watchlist (móvil y ordenador)

Sin sincronización, el watchlist se guarda solo en el navegador donde lo uses. Para sincronizarlo:

1. En GitHub, ve a **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**. Dale acceso **solo a este repositorio** y el permiso **Contents: Read and write**.
2. En la web, ve a **Watchlist → Sincronización**, escribe `alex-j10/stocks-regresion-strategy` y pega el token. Hay que hacerlo una vez en cada dispositivo.

Cada cambio se guarda en `watchlist.json` y relanza el cálculo, que tarda unos minutos. Las métricas de una posición nueva salen aproximadas (con la etiqueta «nuevo») hasta entonces.

### Avisos por email

El proceso diario abre un *issue* en el repositorio en estos casos:

- 🎯 Una posición ha tocado la recta.
- 🔎 Toca la revisión trimestral de una posición.
- 🟢 Hay una nueva señal de compra.

GitHub te los manda por email si en el repositorio pulsas **Watch → Custom → Issues**. Cada aviso sale una sola vez. Se desactivan con `"github_issue_alerts": false`.

## Privacidad

Con el plan gratuito, la web y el repositorio son públicos: cualquiera que tenga la dirección puede ver tu watchlist. Por eso el portal guarda pesos en porcentaje y no importes.

Si quieres que solo tú puedas entrar, pon el dominio en Cloudflare (gratis) y protege el subdominio con **Cloudflare Access** (gratis hasta 50 usuarios). Así solo se entra con tu email.

## Ajustes (`config.json`)

| Clave | Valor | Qué hace |
|---|---|---|
| `length_bars` | 2500 | Length del canal |
| `top_tier_count` | 10 | Cuántas empresas cuentan como Top (compran en −3σ) |
| `buy_sigma_top_tier` / `buy_sigma_other` | −3 / −4 | Zona de compra |
| `watch_sigma` | −3 | A partir de aquí la empresa sale en Vigilar |
| `min_trend_annual_pct` | 0 | Pendiente mínima del canal (% anual) |
| `exclude_if_buy_band_below_zero` | true | Descarta si −3σ o −4σ ≤ 0 |
| `exclude_adrs` | false | `true` quita los ADR (TSM, ASML…) |
| `review_days` | 90 | Cada cuántos días toca la revisión fundamental |
| `max_weight_pct` / `max_positions` | 10 / 20 | Límites de gestión del riesgo |

## Probar en local

```bash
pip install -r requirements.txt
python scripts/build.py --demo         # datos inventados, sin internet
python scripts/build.py --limit 30     # datos reales, solo las 30 mayores
cd dist && python -m http.server       # abre http://localhost:8000
```

No es una recomendación de inversión.
