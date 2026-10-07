# Magnus SDK para Python

[English](README.md) · **Español**

El cliente oficial de Python para **[Magnus Core](https://core.iamagnus.com)**:
agentes de IA gobernados detrás de una API compatible con OpenAI. El modelo
entiende y redacta; las reglas del agente deciden qué pasa y qué acciones
esperan una confirmación, y cada turno deja una traza.

```bash
pip install iamagnus
```

Python 3.9+. Una sola dependencia: `requests`. Si `pip install iamagnus` falla,
el mismo paquete se instala directo desde GitHub: ver
[Instalar sin PyPI](#instalar-sin-pypi).

```python
from iamagnus import MagnusClient

with MagnusClient("https://app.iamagnus.com", "magnus_sys_...") as client:
    agent = client.list_agents()[0]["id"]

    # Un hilo por usuario final: lo continúa `user`, no el historial reenviado.
    chat = client.conversation(agent, user="jane@company.com")

    print(chat.send("Hola, ¿qué puedes hacer?"))
    print(chat.send("¿Y el precio?"))
```

## Instalar sin PyPI

Úsalo cuando el paquete no está en PyPI o la máquina no llega a PyPI. El código
es el mismo y el import también: `from iamagnus import MagnusClient`.

**Desde GitHub.** pip construye el paquete a partir de un tag de este
repositorio. Necesita `git` en la máquina:

```bash
pip install "iamagnus @ git+https://github.com/ABZ-LABS/magnus-python-sdk@v0.1.0"
```

La misma línea sirve en `requirements.txt`, y otras herramientas aceptan la
misma URL:

```text
# requirements.txt
iamagnus @ git+https://github.com/ABZ-LABS/magnus-python-sdk@v0.1.0
```

```bash
uv add "iamagnus @ git+https://github.com/ABZ-LABS/magnus-python-sdk@v0.1.0"
poetry add "git+https://github.com/ABZ-LABS/magnus-python-sdk.git#v0.1.0"
```

Fija un tag, como arriba, para que cada build instale el mismo código. `@main`
sigue al último commit, que no es una versión publicada.

**Sin acceso de red a GitHub** (un CI cerrado, la red de un cliente). Construye
un wheel una vez en una máquina con acceso y entrega el archivo junto con el
proyecto:

```bash
git clone --branch v0.1.0 https://github.com/ABZ-LABS/magnus-python-sdk
pip wheel ./magnus-python-sdk --no-deps -w vendor/
# vendor/iamagnus-0.1.0-py3-none-any.whl va dentro del proyecto

pip install vendor/iamagnus-0.1.0-py3-none-any.whl
```

El wheel no incluye `requests`, que sigue viniendo de tu índice de paquetes. Si
no hay ningún índice, descárgalo junto al wheel con
`pip download requests -d vendor/` e instala con
`pip install --no-index --find-links vendor/ iamagnus`. Corre `pip download` en
el mismo sistema operativo y la misma versión de Python que el destino: algunas
dependencias de `requests` se construyen por plataforma.

## Obtener una API key

1. En el dashboard de Magnus, abre **System API Keys**, en *Integration keys*
   del menú lateral. La ven los administradores de la organización.
2. **Create key**, y elige **qué agente responde** (*Which agent should
   answer?*). Una key se crea para un agente y siempre responde como ese
   agente: `list_agents()` devuelve exactamente ese, y nombrar otro agente de
   tu organización se rechaza con `model_not_allowed`. Las keys para tus
   propios agentes requieren un plan pago; los agentes de muestra están
   abiertos en todos los planes.
3. En **What will use this key?**, deja **My app or backend**. Esa elección
   queda registrada en cada turno que corre la key, así que conviene una key
   por integración en lugar de una compartida.
4. **Cópiala en el momento.** Magnus guarda solo un hash y muestra la key una
   sola vez.

Las keys empiezan con `magnus_sys_` (`magnus_gpt_` si se crearon para un
cliente de chat como OpenWebUI).

> **No confundir con "LLM API Keys".** Esa pantalla guarda *tus* credenciales
> de OpenAI, Anthropic u otro proveedor, para que Magnus llame a los modelos en
> tu nombre. No te autentican contra Magnus; usar una aquí da 401.

## Apuntar el cliente a un despliegue

La URL base es configuración, no una constante: la biblioteca no trae nada de
`iamagnus.com` incorporado. El servicio alojado es `https://app.iamagnus.com`,
la misma dirección que el dashboard. Pasa la **raíz del servidor**, sin `/v1`:
el cliente arma `/v1/...` por su cuenta, más `/api/health/simple`, que vive
fuera de ese prefijo.

```python
hosted = MagnusClient("https://app.iamagnus.com", "magnus_sys_...")
local  = MagnusClient("http://localhost:5001",    "magnus_sys_...")

# En un despliegue, lee las dos del entorno:
import os
client = MagnusClient(os.environ["MAGNUS_BASE_URL"], os.environ["MAGNUS_API_KEY"])
```

Una barra final se recorta, y una URL vacía falla al construir el cliente en
lugar de aparecer como un error de transporte ilegible.

### Probar la URL y la key por separado

```python
client.health()        # sin key: prueba que la URL es correcta
client.list_agents()   # usa la key: prueba la credencial
```

Si `health()` funciona y `list_agents()` devuelve 401, el problema es la key,
no la URL, y viceversa.

## En qué se diferencia de OpenAI

**La key elige el agente.** `model` no decide quién responde; lo decide el
agente de la key. Nombrar otro agente de la organización se rechaza
(`model_not_allowed`), y cualquier otro valor, como `gpt-4o`, se ignora, así
que un cliente de OpenAI funciona sin cambios.

**Un hilo es el usuario final, no el historial.** El servidor lee solo el
último mensaje del usuario y guarda la memoria y el estado de la conversación
de su lado, así que reenviar el historial no restaura nada. Hay un hilo vivo
por (API key, `user`, agente): el mismo `user` lo continúa, y termina tras 30
minutos sin actividad. **Pasa siempre `user`**: sin él, todos los que llaman
con la key comparten un mismo hilo. Cada respuesta informa en qué sesión corrió
el servidor, pero devolverle un id de sesión no permite elegir, retomar ni
reiniciar un hilo.

**El agente es dueño del turno.** `tools`, `tool_choice`, `functions`,
`function_call`, `response_format` y `n > 1` se *rechazan*, no se ignoran: las
herramientas se configuran por agente y el formato de la respuesta lo decide el
agente. `temperature`, `max_tokens`, `top_p`, `stop`, `seed` y
`presence_penalty` se aceptan y se ignoran: también los maneja el agente.
Algunos clientes de OpenAI mandan `tool_choice: "auto"` o
`response_format: {"type": "text"}` por defecto; cuentan como definidos y se
rechazan, así que quítalos.

**Algunos límites responden 200.** Cuando un usuario final, la organización o
su plan se quedan sin turnos, el turno devuelve HTTP 200 con una frase en lugar
de una respuesta, `usage_source: "estimated"` y sin trace id; no un 429. La
lista está en [CONTRACT.es.md](CONTRACT.es.md#límites-que-responden-200).

**Una persona puede tomar la conversación.** Cuando el agente deriva a alguien
de tu equipo, o lo toman desde el panel, el agente deja de responder hasta que
se la devuelvan. Cada turno sigue devolviendo 200 —primero el mensaje de
derivación del agente, después un aviso fijo— y `chat.handoff` es `True`
mientras una persona esté a cargo. Las respuestas del operador todavía no
llegan por la API.

**Un turno en streaming puede fallar después del HTTP 200.** Una vez que salió
el primer fragmento, la línea de estado ya no se puede cambiar, así que el
fallo llega *dentro* del stream. Este cliente lanza `StreamError` en lugar de
entregar una respuesta truncada como si fuera un éxito.

## Streaming

```python
stream = chat.stream("Cuéntame más")

for delta in stream:
    print(delta, end="", flush=True)

print(stream.text, stream.session_id, stream.magnus["usage_source"])
```

Dos formas son normales y las dos se manejan: token por token, y un único delta
para un turno que el servidor entrega entero. `include_usage=True` agrega el
fragmento final con `stream.usage`.

Un turno que falla a mitad del stream lanza la excepción fuera del bucle:

```python
from iamagnus import StreamError

try:
    for delta in stream:
        print(delta, end="")
except StreamError as error:
    # error.partial_text es lo que el lector ya vio
    print(error.code, error.message)
```

## Errores

Cada fallo trae el sobre de error del servidor:

```python
import time

from iamagnus import AuthenticationError, RateLimitError, UnsupportedParameterError

try:
    client.chat(agent, messages)
except RateLimitError as error:
    time.sleep(error.retry_after or 5)
except UnsupportedParameterError as error:
    print(f"Magnus rechaza {error.param!r}")
except AuthenticationError:
    raise SystemExit("la API key no es aceptada")
```

| Clase | Estado |
|---|---|
| `InvalidRequestError` | 400, incluido `code: model_not_allowed` (la key es de otro agente) |
| `UnsupportedParameterError` | 400, `code: unsupported_parameter` (subclase de la anterior) |
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403, la organización de la key no existe o está desactivada |
| `NotFoundError` | 404 |
| `ConflictError` | 409, un turno con este `Idempotency-Key` sigue corriendo |
| `RateLimitError` | 429, ver `.retry_after` |
| `ServerError` | 5xx |
| `MagnusConnectionError` / `MagnusTimeoutError` | nunca llegó a Magnus, o dejó de esperar |
| `StreamError` | el turno falló después de abrirse el stream |

Todas son subclases de `MagnusError`. `.status`, `.type`, `.code`, `.param`,
`.headers` y `.message` traen las palabras del propio servidor.

## Reintentos e idempotencia

Un turno hace avanzar la conversación y puede correr herramientas con efectos,
así que reintentarlo a ciegas puede duplicarlos. Por eso este cliente
reintenta:

- **GET** siempre, ante 429/5xx y fallos de transporte;
- **POST** solo si pasaste un `idempotency_key`, porque entonces el servidor
  repite su primera respuesta en lugar de correr el turno otra vez;
- **nunca un stream**: un cuerpo en streaming no se puede repetir.

Se respeta `Retry-After`; si no viene, la espera crece exponencialmente con
variación aleatoria.

```python
import uuid
client.chat(agent, messages, idempotency_key=str(uuid.uuid4()))
```

Usa un UUID nuevo en cada turno: el servidor compara la key en toda la
organización durante 24 horas, sin mirar el cuerpo ni el usuario final.

## Medición

`response["usage"]` trae los conteos reales de tokens del proveedor cuando
`response["magnus"]["usage_source"] == "measured"`. `"estimated"` significa que
el turno nunca llegó a un LLM, lo que incluye los límites que responden 200, y
los números son una heurística de `len/4`. **No factures sobre una
estimación.**

`client.rate_limit_remaining` guarda el último cupo visto para la key.

## Multi-tenencia

`user="jane@company.com"` en el cliente, en la conversación o en una llamada.
Define el campo `user` de OpenAI, y es lo que separa a tus usuarios finales:
cada valor es una persona, con su propio hilo y su memoria, y una llamada sin
él cae en el único hilo que comparten todos los de la key. Lo que va antes de
una `@` pasa a ser el nombre que ve el agente. Los valores dependen de la key:
una key nueva o rotada hace empezar de cero a cada persona.

## Verificar un despliegue

`magnus-livecheck` corre los catorce chequeos de [CONTRACT.es.md](CONTRACT.es.md)
contra un despliegue real y sale con un código distinto de cero salvo que pasen
todos:

```bash
export MAGNUS_BASE_URL=https://app.iamagnus.com
export MAGNUS_API_KEY=magnus_sys_...   # una key creada para un agente de prueba

magnus-livecheck
```

Los chequeos 5, 8, 9, 10 y 13 corren turnos reales, que gastan tokens y quedan
registrados como cualquier conversación. Una key responde solo como su propio
agente, así que crea la key para un agente de prueba.

## API

| | |
|---|---|
| `MagnusClient(base_url, api_key, *, user, timeout, max_retries, auth_scheme, session)` | |
| `health()` | prueba de alcance sin autenticación |
| `list_agents()` / `get_agent(id)` | agentes; un id desconocido es `None` |
| `chat(agent, messages, *, session_id, idempotency_key, user, extra_body)` | un turno completo |
| `stream_chat(agent, messages, *, session_id, user, include_usage)` | un turno en streaming |
| `send_message(agent, content, ...)` | entra texto, sale texto |
| `conversation(agent, *, user, session_id)` | un hilo para un usuario final: `.send()`, `.stream()`, `.reset()`; después de cada turno `.last_trace_id`, `.last_usage_source` y `.handoff` |

`extra_body` reenvía campos del servidor más nuevos que esta biblioteca. Cada
detalle del cable está en [CONTRACT.es.md](CONTRACT.es.md).

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
```

La suite corre contra un Magnus falso que implementa
[CONTRACT.md](CONTRACT.md) sobre sockets reales, así que el framing de SSE y la
transferencia por fragmentos se ejercitan de verdad. Las versiones se describen
en [RELEASING.es.md](RELEASING.es.md).

## Licencia

[Apache License 2.0](LICENSE). Ver [NOTICE](NOTICE) para la atribución.
