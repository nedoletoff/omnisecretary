SHELL := /bin/bash
COMPOSE := docker compose --project-directory . -f docker-compose.yml --env-file .env

.PHONY: help init up down restart ps logs smoke backup update reload pull shell-owui shell-pg shell-qdrant presets vikunja-admin init-webui tg-up tg-down tg-logs mcp-build mcp-init mcp-logs vault-logs voice-up voice-piper voice-down voice-logs

help: ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

init: ## Первичная установка на Pi (идемпотентно)
	./scripts/bootstrap-pi.sh

up: ## Запустить стек
	$(COMPOSE) up -d --remove-orphans

down: ## Остановить стек (данные сохраняются)
	$(COMPOSE) down

restart: ## Перезапустить стек
	$(COMPOSE) restart

ps: ## Статус контейнеров
	$(COMPOSE) ps

logs: ## Логи всех сервисов (Ctrl+C для выхода)
	$(COMPOSE) logs -f --tail=100

smoke: ## Smoke-тест стека
	./scripts/smoke-test.sh

backup: ## Бэкап БД + Qdrant + вольта
	./scripts/backup.sh

update: ## Обновить образы по фиксированным тегам
	./scripts/update.sh

reload: ## Перечитать конфиг nginx без простоя
	$(COMPOSE) exec nginx nginx -s reload

pull: ## Скачать образы
	$(COMPOSE) pull

shell-owui: ## Shell в контейнере Open WebUI
	$(COMPOSE) exec open-webui bash

shell-pg: ## psql в PostgreSQL
	$(COMPOSE) exec postgres psql -U $$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2) -d $$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)

shell-qdrant: ## Shell в контейнере Qdrant
	$(COMPOSE) exec qdrant bash

vikunja-admin: ## Создать администратора Vikunja (идемпотентно)
	./scripts/init-vikunja.sh

init-webui: ## Владелец, API-ключ, провайдер LLM, пресеты (идемпотентно)
	./scripts/init-webui.sh

presets: ## Импорт пресетов: make presets ARGS="--base-model <id>"
	./scripts/import-presets.sh $(ARGS)

tg-up: ## Запустить Telegram-бота (профиль telegram)
	$(COMPOSE) --profile telegram up -d telegram-bot

tg-down: ## Остановить Telegram-бота
	$(COMPOSE) --profile telegram stop telegram-bot

tg-logs: ## Логи Telegram-бота
	$(COMPOSE) --profile telegram logs -f --tail=100 telegram-bot

mcp-build: ## Собрать образ MCP-серверов
	$(COMPOSE) build mcp-vault

mcp-init: ## Зарегистрировать MCP-серверы в Open WebUI (идемпотентно)
	./scripts/init-mcp.sh

mcp-logs: ## Логи всех MCP-серверов
	$(COMPOSE) logs -f --tail=50 mcp-vault mcp-files mcp-github mcp-vikunja mcp-sandbox

vault-logs: ## Логи формы подключения вольта
	$(COMPOSE) logs -f --tail=50 vault-manager

voice-up: ## Запустить Vosk STT + Silero TTS
	$(COMPOSE) --profile voice up -d stt tts

voice-piper: ## Запустить STT + Piper TTS (лёгкий)
	$(COMPOSE) --profile voice --profile voice-piper up -d stt tts-piper

voice-down: ## Остановить голосовые сервисы
	$(COMPOSE) --profile voice --profile voice-piper stop stt tts tts-piper

voice-logs: ## Логи STT и TTS
	$(COMPOSE) --profile voice --profile voice-piper logs -f --tail=50 stt tts tts-piper
