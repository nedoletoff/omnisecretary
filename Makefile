SHELL := /bin/bash
COMPOSE := docker compose --project-directory . -f docker-compose.yml --env-file .env

.PHONY: help init up down restart ps logs smoke backup update reload pull shell-owui shell-pg shell-qdrant presets vikunja-admin

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

presets: ## Импорт пресетов: make presets ARGS="--base-model <id>"
	./scripts/import-presets.sh $(ARGS)
