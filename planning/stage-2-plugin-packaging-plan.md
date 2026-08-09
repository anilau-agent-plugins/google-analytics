# Этап 2 — детальный план упаковки плагина

Статус: подготовлен, ожидает утверждения пользователя  
Дата проверки источников и локального окружения: 2026-08-09

## 1. Цель этапа

Упаковать текущую спецификацию как устанавливаемый локальный плагин **Google Analytics Advisor**
для Codex и Claude Code с одним общим skill, явными manifests и локальными marketplace-каталогами.
Этап подтверждает только корректность структуры, обнаружение skill и честное описание будущих
возможностей. Он не реализует Python runtime, OAuth, обращения к Google API, аудит, отчёты или
изменения GA4/GTM/сайта.

Этап реализуется только после отдельного утверждения этого документа.

## 2. Проверенное исходное состояние

- Канонический каталог: `C:\dev\tools\google-analytics`.
- Этап 1 завершён; в каталоге есть только спецификации, JSON Schemas, fixtures и их валидатор.
- Общий стандарт проекта: `C:\dev\tools\PLUGIN_STANDARD.md`.
- `C:\Users\edvol\.agents\plugins`, `C:\Users\edvol\plugins`,
  `C:\Users\edvol\.claude\plugins` и `C:\dev\tools\.claude-plugin` отсутствуют; коллизий для
  планируемых локальных каталогов сейчас нет.
- Установлен `codex-cli 0.88.0`; команда `codex plugin` в этой версии отсутствует.
- Claude Code CLI не установлен.

Перед реализацией состояние проверить повторно: пользователь или обновление платформы могли создать
эти каталоги либо изменить доступные команды.

## 3. Актуальные платформенные ограничения

### Codex

- Корень плагина должен содержать `.codex-plugin/plugin.json`.
- Общие skills размещаются в `skills/<skill-id>/SKILL.md`.
- Manifest paths должны быть относительными корню плагина и начинаться с `./`.
- Персональный marketplace хранится в `~/.agents/plugins/marketplace.json`; для локального источника
  используется относительный source path и отдельная marketplace-совместимая ссылка на канонический
  каталог.
- Точный набор допустимых manifest-полей определяется актуальным локальным валидатором. Поля,
  отвергаемые установленной версией валидатора, не добавляются.

### Claude Code

- Для явной переносимой идентичности создаётся `.claude-plugin/plugin.json`, хотя Claude Code может
  обнаруживать некоторые плагины и без него.
- Skill находится в `skills/google-analytics/SKILL.md` и вызывается в namespace
  `google-analytics:google-analytics`.
- Локальный marketplace имеет `.claude-plugin/marketplace.json`; относительный plugin source
  начинается с `./`.
- Установленный плагин копируется в cache Claude Code, поэтому ни skill, ни manifest не могут
  зависеть от файлов за пределами каталога `google-analytics`.
- Live-валидация выполняется документированной командой `claude plugin validate .` на актуальном CLI. До установки CLI
  её результат нельзя считать подтверждённым.

## 4. Архитектурные решения этапа

### 4.1 Единый канонический пакет

Редактируется только `C:\dev\tools\google-analytics`. Codex и Claude Code получают один и тот же
`skills/google-analytics/SKILL.md`; платформенные копии skill не создаются.

Целевая структура после этапа:

```text
C:\dev\tools\google-analytics\
├── .codex-plugin\
│   └── plugin.json
├── .claude-plugin\
│   └── plugin.json
├── skills\
│   └── google-analytics\
│       ├── SKILL.md
│       └── agents\
│           └── openai.yaml
├── planning\
│   ├── ...материалы этапа 1...
│   └── stage-2-plugin-packaging-plan.md
├── DEVELOPMENT_PLAN.md
├── README.md
├── LICENSE
└── CHANGELOG.md
```

Каталоги `scripts/`, `tests/`, `references/`, `assets/`, MCP, apps и hooks на этом этапе не
создаются: для них ещё нет реализованного содержимого. Диагностический Python CLI также переносится
в этап 3, чтобы не смешивать упаковку с runtime и обнаружением Python.

### 4.2 Manifest metadata

Оба manifest используют:

- техническое имя `google-analytics`;
- display name `Google Analytics Advisor`;
- стартовую внутреннюю версию `0.1.0`;
- автора `Eduard Volkov`, developer `Anilau`, homepage `https://anilau.com`;
- краткое честное описание помощника по GA4 для неспециалистов;
- путь к skills `./skills/` только в синтаксисе, принятом конкретной платформой.

Версии в двух manifests и `CHANGELOG.md` совпадают. Repository URL, privacy URL, terms URL и
публичные marketplace identifiers не придумываются: они добавляются на этапе 12 после создания и
проверки реальных ресурсов. Поле лицензии добавляется только если текущая схема принимает
`LicenseRef-Anilau-Commercial`; иначе коммерческий статус фиксируется в `LICENSE` и `README.md`, а
не маскируется неподходящим SPDX identifier.

Для Codex UI metadata создаётся `skills/google-analytics/agents/openai.yaml` штатным генератором
`skill-creator`, затем сверяется с общим skill. Starter prompts описывают назначение продукта, но не
утверждают, что Google API уже подключён.

### 4.3 Skill-каркас без фиктивной функциональности

`SKILL.md` содержит только переносимый YAML frontmatter `name` и `description`, а в теле:

- роль понятного советника для неспециалиста;
- маршрутизацию будущих сценариев на утверждённые этапы;
- правило объяснять цель, доказательства, ограничения и безопасный следующий шаг;
- запрет заявлять, что GA4/GTM подключены или изменены без CLI evidence;
- явный ответ для ещё не реализованных операций: функция пока недоступна в текущей версии, какой
  будущий этап её добавит и что пользователь может подготовить без передачи секретов;
- ссылки только на необходимые документы внутри каталога плагина.

Skill не содержит команд несуществующего CLI, фиктивных API results и инструкции вставлять OAuth
секреты в чат.

### 4.4 Локальный marketplace Codex

После повторной проверки коллизий:

1. Создать каталог `C:\Users\edvol\plugins`, если он отсутствует.
2. Создать junction
   `C:\Users\edvol\plugins\google-analytics -> C:\dev\tools\google-analytics`.
3. Создать или безопасно дополнить `C:\Users\edvol\.agents\plugins\marketplace.json`.
4. Добавить запись `google-analytics` с source path `./plugins/google-analytics`, категорией,
   installation policy и authentication policy.

Существующий marketplace JSON, если он появится, не перезаписывается: он сначала парсится, его
точное исходное содержимое сохраняется как локальный rollback snapshot вне публикуемого плагина,
затем меняется только целевая запись с сохранением остальных. Существующий каталог/ссылка с другим
target считается блокером. Удаление marketplace entry никогда не удаляет канонический каталог.

### 4.5 Локальный marketplace Claude Code

Общий каталог `C:\dev\tools` используется как локальный Claude marketplace:

```text
C:\dev\tools\.claude-plugin\marketplace.json
```

В нём запись `google-analytics` указывает на `./google-analytics`. Это не требует второй копии или
junction и в будущем позволяет добавлять плагины проекта по одному. Файл создаётся или дополняется
без изменения `google-ads`, `yandex-direct` и `yougile`.

Когда совместимый Claude Code CLI доступен, marketplace добавляется штатной командой, затем отдельно
устанавливается конкретный плагин. Добавление marketplace не считается установкой плагина.

### 4.6 Документация пакета

- `README.md`: текущий статус preview, назначение, поддерживаемые платформы, локальная установка,
  обновление, удаление, диагностика и явно не реализованные возможности.
- `LICENSE`: текст из draft этапа 1, помеченный как коммерческая лицензия, требующая юридической
  проверки до продажи; без заявления о готовности к коммерческому релизу.
- `CHANGELOG.md`: версия `0.1.0`, содержащая только packaging/skill skeleton.

Публикация GitHub-репозитория, продажа, customer access и production marketplace не выполняются.
README лишь фиксирует будущую схему установки: приватный GitHub repository и отдельный marketplace
repository организации после этапа 12.

## 5. Последовательность реализации после утверждения

1. Повторно проверить официальные страницы Codex/Claude, доступные CLI, локальные validators и
   отсутствие коллизий в marketplace paths.
2. Зафиксировать снимок дерева `google-analytics` и повторно запустить acceptance этапа 1.
3. Создать общий skill штатным `init_skill.py`, оставив только реально нужные файлы; сгенерировать и
   проверить `agents/openai.yaml`.
4. Создать отдельные Codex и Claude manifests с общими identity/version и платформенно допустимыми
   полями.
5. Добавить package README, LICENSE и CHANGELOG без обещаний ещё не реализованных функций.
6. Прогнать `quick_validate.py` для skill и `validate_plugin.py` для Codex package; исправлять схему,
   а не обходить validator.
7. Безопасно создать Codex junction и personal marketplace entry, затем проверить resolved target и
   повторный запуск установки без дублирования.
8. Создать/дополнить Claude local marketplace в `C:\dev\tools` и статически проверить JSON,
   относительные paths, manifests и отсутствие внешних зависимостей.
9. Если на момент реализации доступен совместимый Codex plugin CLI, выполнить штатную установку,
   listing и проверку обнаружения skill в новой задаче. Иначе зафиксировать `blocked_live_codex_cli`
   с обнаруженной версией, сохранив успешные статические результаты отдельно.
10. Если доступен Claude Code CLI, выполнить `claude plugin validate .`, добавить marketplace,
    установить plugin и проверить namespace после reload. Иначе зафиксировать
    `blocked_live_claude_cli`.
11. Проверить, что запросы о GA4 корректно активируют skill-каркас и честно сообщают о границе этапа,
    не вызывая Google API и не создавая credentials/runtime data.
12. Повторно выполнить acceptance этапа 1 и secret/path scan; проверить, что существующие навыки не
    изменены.
13. Обновить статус этапа 2 только на основании выполненных критериев и представить evidence
    пользователю. Если хотя бы одна целевая платформа не прошла live install/discovery, использовать
    статус `implemented_pending_live_validation`, а не `completed`. Не начинать этап 3 без отдельного
    детального плана и утверждения.

## 6. Критерии приёмки

### Обязательные для реализации артефактов

- Оба manifest и общий `SKILL.md` существуют, синтаксически валидны и согласованы по имени/версии.
- `SKILL.md` проходит `quick_validate.py`; Codex package проходит доступный официальный/local
  `validate_plugin.py`.
- Все manifest/source paths относительны, разрешаются внутри ожидаемого package/marketplace root и
  не содержат абсолютных путей разработчика.
- Codex junction разрешается строго в `C:\dev\tools\google-analytics`; повторная настройка
  идемпотентна и не повреждает существующие marketplace entries.
- Claude marketplace JSON валиден и source `./google-analytics` указывает на канонический пакет.
- Ни один файл не содержит OAuth clients, токенов, customer data или фиктивных API результатов.
- Не созданы Python runtime, Analytics CLI, OAuth flow или сетевые функции.
- Валидатор этапа 1 продолжает проходить полностью.
- `google-ads`, `yandex-direct` и `yougile` не изменены.
- README честно отделяет реализованную упаковку от будущей функциональности.

### Обязательные для статуса `completed`

- На совместимой версии Codex выполнены marketplace/install/list/discovery и проверено обнаружение
  skill в новой задаче.
- На совместимой версии Claude Code выполнены `claude plugin validate .`, marketplace add, install,
  reload и проверка skill namespace.
- Если любой CLI отсутствует или установленная версия не поддерживает plugins, это фиксируется как
  отдельный внешний blocker с точной версией, а этап получает статус
  `implemented_pending_live_validation`. Статическая приёмка не переименовывается в live, и
  кроссплатформенная совместимость не объявляется подтверждённой. Установка или обновление CLI
  требует отдельного согласия пользователя.

## 7. Rollback

- Если target marketplace entry отсутствовала до этапа, удалить только созданную entry,
  предварительно повторно проверив её имя и source. Если entry существовала и была изменена,
  восстановить её точный предварительный JSON из rollback snapshot; не удалять её.
- Удалить junction только если его resolved target по-прежнему равен каноническому каталогу.
- Удаление junction, marketplace entry или установленной cache-копии не удаляет
  `C:\dev\tools\google-analytics`.
- Не затрагивать будущие credentials/runtime data; на этапе 2 они вообще не должны появиться.
- Файлы пакета откатывать точечным patch по evidence текущего этапа, без destructive Git-команд.

## 8. Риски и решения

- **Расхождение документации и локального CLI.** Использовать актуальные official docs для проекта,
  но принимать manifest только после проверки доступным validator; live status сообщать отдельно.
- **Изменение manifest schemas.** Перед реализацией повторно сверить схемы; не сохранять неизвестные
  поля ради желаемого metadata.
- **Коллизия локального marketplace.** Не перезаписывать файл/каталог/ссылку с другим назначением;
  остановиться и запросить решение пользователя.
- **Ложное впечатление готового продукта.** Версия, README, changelog и ответы skill явно обозначают
  packaging preview и границу этапа 2.
- **Зависимость Claude от внешних файлов.** Все ресурсы runtime-плагина остаются внутри
  `google-analytics`; общий `PLUGIN_STANDARD.md` используется разработчиком, но не требуется
  установленной cache-копии.

## 9. Источники для повторной проверки

- OpenAI plugin packaging: https://developers.openai.com/plugins/build/plugins
- OpenAI skills: https://developers.openai.com/plugins/build/skills
- Локальные `plugin-creator`, `skill-creator` и их validators.
- Claude Code plugin reference: https://code.claude.com/docs/en/plugins-reference
- Claude Code marketplaces: https://code.claude.com/docs/en/plugin-marketplaces
- Claude Code skills: https://code.claude.com/docs/en/skills
