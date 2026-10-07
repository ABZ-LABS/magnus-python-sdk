# Publicar una versión

[English](RELEASING.md) · **Español**

Las versiones se publican en PyPI como [`iamagnus`](https://pypi.org/project/iamagnus/)
mediante el workflow `Release`, con la publicación de confianza de PyPI (*trusted
publishing*): en este repositorio no vive ningún token.

## Configuración, una sola vez

1. En PyPI, en *Your account → Publishing*, agrega un **pending trusted
   publisher**: proyecto `iamagnus`, owner `ABZ-LABS`, repositorio
   `magnus-python-sdk`, workflow `release.yml`, environment `pypi`.
2. En GitHub, en *Settings → Environments*, crea un environment llamado `pypi`.
   Si exiges un revisor ahí, cada publicación pasa a ser una aprobación de un
   clic.

## En cada versión

1. Pon la misma versión en `pyproject.toml` y en `iamagnus/__init__.py`.
2. Corre `magnus-livecheck` contra la API de producción con un agente de prueba.
   Tienen que pasar los quince chequeos.
3. Haz el commit, crea el tag y súbelo:

   ```bash
   git tag v0.2.0
   git push origin main v0.2.0
   ```

El workflow rechaza un tag que no coincida con las dos cadenas de versión, corre
la suite, construye el paquete, revisa los metadatos con `twine check --strict`
y publica.

## La instalación desde GitHub depende del tag

La sección *Instalar sin PyPI* del README instala desde un tag de este
repositorio, así que funciona en cuanto el repositorio es público y el tag está
subido, haya aceptado PyPI la publicación o no. Cuando cambie la versión,
actualiza el tag en esa sección de `README.md` y de `README.es.md`, y en el
dashboard de Magnus (`sdk_links_section.dart` en el front end).

Si `CONTRACT.md` cambió, cambia igual en los SDKs de Node y Go, junto con su
traducción `CONTRACT.es.md`.
