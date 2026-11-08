# Ключи доступа к серверу стенда ЦДА

Сохранены по просьбе пользователя (2026-11-08, перезапуск роя не должен
потерять доступ). Файлы коммитятся в репозиторий workspace вместе с проектом.

- `id_ed25519` / `id_ed25519.pub` — приватный/публичный ключ SSH к серверу
  стенда (cda.oblaka.pw, пользователь и адрес — см. `sshx.py`).
- `sshx.py` — хелпер Артёма Захарова для выполнения команд на сервере:
  `python3 deploy/keys/sshx.py "cmd"` и копирование файлов
  `python3 deploy/keys/sshx.py put <локальный> <удалённый>` (put — аргумент,
  а не команда).

## Использование

- SSH-команды на сервере: `python3 deploy/keys/sshx.py "docker ps"`.
- Деплой: `CDA_KEY=deploy/keys/id_ed25519 python3 deploy/deploy_remote.py`
  (deploy_remote.py берёт ключ из env CDA_KEY).
- Контейнеры стенда: `deploy-api-1` (API, `/srv/app`), `deploy-db-1`
  (PostgreSQL, `psql -U cda -d cda`), `deploy-frontend-1`.

## Безопасность

Ключ открывает только сервер стенда ЦДА пользователя. Если репозиторий
покинет доверенный контур — отозвать ключ на сервере (`~/.ssh/authorized_keys`).
