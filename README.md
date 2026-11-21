# dual-agent-task

Локальний CLI-harness, який координує Codex і Claude Code навколо спільних
артефактів: task-файлу, структурованого review, git diff і результатів тестів.

Поточний MVP реалізує перший вертикальний зріз:

- `init` — додає конфігурацію harness у наявний проєкт;
- `upgrade` — додає нові секції та assets без перезапису локальних правил;
- `doctor` — перевіряє Git, конфігурацію, CLI та авторизацію агентів;
- `plan` — окремо запускає цикл author/reviewer і створює task-файл;
- `implement` — окремо реалізує підтверджений task і запускає code review;
- `task show` та `task approve` — показують і підтверджують task.

Harness не використовує OpenAI або Anthropic API напряму. Він запускає локальні
`codex` і `claude`, тому спосіб білінгу визначається авторизацією цих CLI.

## Вимоги

- Python 3.9+
- Git
- Codex CLI
- Claude Code CLI

Перевірка авторизації:

```bash
codex login status
claude auth status
```

## Локальний запуск

Із кореня цього репозиторію:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/dual-agent-task --help
```

Пакування використовує `setup.py`, тому editable-install працює також зі
штатним macOS `pip 21.2.4`; оновлювати pip і завантажувати build-залежності для
цього не потрібно.

Або без встановлення:

```bash
PYTHONPATH=src python3 -m dual_agent_task --help
```

## Ініціалізація робочого проєкту

```bash
dual-agent-task init /path/to/project
dual-agent-task doctor /path/to/project
```

Оновлення вже ініціалізованого проєкту після оновлення самого harness:

```bash
dual-agent-task upgrade /path/to/project
dual-agent-task doctor /path/to/project
```

`upgrade` не перезаписує `PROJECT_RULES.md`, існуючі промпти, схеми чи
verification-команди. Він лише додає відсутні файли й конфігураційні секції.

Команда `init` створить:

```text
.dual-agent-task/
  config.toml
  prompts/
  schemas/
  runs/              # ігнорується Git
  state/             # локальний стан етапів, ігнорується Git
tasks/
PROJECT_RULES.md
```

Існуючі файли не перезаписуються. Промпти, схеми й конфігурацію варто комітити;
`.dual-agent-task/runs/` містить локальні журнали запусків, а
`.dual-agent-task/state/` — локальний стан implementation stages.

## Моделі та effort

Модель і effort задаються окремо для кожної ролі:

```toml
[models]
spec_author = "sonnet"
spec_reviewer = "gpt-5.6-sol"
implementer = "gpt-5.6-sol"
code_reviewer = "sonnet"

[effort]
spec_author = "high"
spec_reviewer = "high"
implementer = "medium"
code_reviewer = "high"
```

Для Claude harness передає `--model` і `--effort`. Для Codex він передає
`--model` та `--config model_reasoning_effort=...`. Якщо значення відсутнє,
відповідний CLI використовує власний default.

## Два незалежні режими

Формулювання задачі та її виконання — це два окремі запуски. Harness не
переходить до реалізації автоматично після створення task.

### 1. Формулювання задачі

Інтерактивно:

```bash
dual-agent-task plan --project /path/to/project
```

З файлу:

```bash
dual-agent-task plan \
  --project /path/to/project \
  --input feature-description.md
```

Режим розбиття на implementation stages задається під час формулювання task:

```bash
# Claude сам вирішує, чи потрібні етапи; Codex перевіряє це рішення (default)
dual-agent-task plan --stages auto --project /path/to/project

# Task обов'язково повинен містити щонайменше два етапи
dual-agent-task plan --stages required --project /path/to/project

# Task має залишитися атомарним
dual-agent-task plan --stages disabled --project /path/to/project
```

`--staged` є коротким alias для `--stages required`, а `--no-stages` — для
`--stages disabled`. Кожен створений етап має стабільний `stage_id`, залежності,
власні scope, acceptance criteria та test plan.

Для task, написаних вручну, використовується такий формат:

```markdown
## Implementation stages

### Stage 1: Domain model

stage_id: domain-model
depends_on: []

#### Goal
...
#### Scope
...
#### Acceptance criteria
...
#### Test plan
...

### Stage 2: User interface

stage_id: user-interface
depends_on: [domain-model]

#### Goal
...
#### Scope
...
#### Acceptance criteria
...
#### Test plan
...
```

Після agent review task матиме один зі статусів:

- `ready_for_human` — агенти погодили task;
- `needs_user_input` — потрібне продуктне рішення;
- `review_incomplete` — вичерпано ліміт раундів.

Підтвердження:

```bash
dual-agent-task task show /path/to/project/tasks/my-feature.md
dual-agent-task task approve /path/to/project/tasks/my-feature.md
```

Команда `task approve` є обов'язковим людським checkpoint. `plan` ніколи не
запускає реалізацію самостійно.

Якщо task уже сформульований і погоджений поза harness, його можна прийняти без
повторного запуску planning-агентів:

```bash
dual-agent-task task adopt existing-task.md --project /path/to/project
```

Команда створює approved-копію в `tasks/`, зберігає посилання на початковий
файл і не змінює його зміст. Сам `implement` автоматично не запускається.

### 2. Виконання підтвердженої задачі

Запускається окремою командою:

```bash
dual-agent-task implement \
  --project /path/to/project \
  tasks/my-feature.md
```

Перед стартом harness перевіряє статус `approved` і чистоту Git worktree
(незакоміченим може залишатися сам task-файл). Далі:

1. Codex реалізує task у `workspace-write` sandbox.
2. Harness перевіряє, що `HEAD` не змінився.
3. Harness запускає команди з `[verification]` у `config.toml`.
4. Claude у read-only режимі перевіряє task, diff, файли та результати команд.
5. Findings повертаються Codex до вичерпання ліміту раундів або approval.

Успішний результат має статус `ready_for_human_review`. Harness не створює
commit: після перевірки ви робите його самостійно.

### Поетапне виконання

Переглянути етапи та локальний стан:

```bash
dual-agent-task task stages \
  --project /path/to/project \
  tasks/my-feature.md

dual-agent-task task stages --json \
  --project /path/to/project \
  tasks/my-feature.md
```

Запустити конкретний етап за ID, номером або вибрати наступний доступний:

```bash
dual-agent-task implement --stage domain-model \
  --project /path/to/project tasks/my-feature.md

dual-agent-task implement --stage 2 \
  --project /path/to/project tasks/my-feature.md

dual-agent-task implement --stage next \
  --project /path/to/project tasks/my-feature.md
```

Task із кількома етапами не запускається без `--stage`. Codex отримує повний
task як контекст, але реалізує лише поточний етап; Claude перевіряє scope етапу,
регресії завершених етапів і відсутність передчасної реалізації наступних.

Після статусу `ready_for_human_review` перегляньте зміни та створіть commit
самостійно. Потім позначте етап завершеним:

```bash
dual-agent-task task complete-stage \
  --project /path/to/project \
  tasks/my-feature.md domain-model
```

`complete-stage` вимагає approved review, чистий working tree і новий commit.
Залежні етапи розблоковуються лише після цього checkpoint. Локальний стан
зберігається в `.dual-agent-task/state/` та ігнорується Git.

### Прогрес у терміналі

За замовчуванням `plan` та `implement` показують live-прогрес із timestamps:
поточний агент і модель, читання та редагування файлів, verification-команди,
heartbeat для довгих операцій, тривалість і фінальний verdict. Harness не
вигадує відсоток завершення, оскільки agent CLI його не надають.

```text
16:42:03 [1/3] Codex implementing · gpt-5.6-sol/medium
16:42:27       Editing Streamer/StreamSetup/Contest/Sport.swift
16:43:08       Running xcodebuild test ...
16:45:31       Still working… 03:28
16:48:02 ✓     Codex finished · 05:59 · 15 files changed
16:48:02 [1/3] Verification 1/2 · git diff --check
16:48:03 ✓     Passed · 00:01
16:50:17 [1/3] Claude reviewing · sonnet/high
16:52:11 ✓     Review finished · approved · 01:54
```

Доступні режими:

```bash
# Стандартний компактний live-прогрес
dual-agent-task implement --project /path/to/project tasks/my-feature.md

# Текстовий формат для CI та лог-файлів
dual-agent-task implement --progress plain \
  --project /path/to/project tasks/my-feature.md

# Повні JSONL-події агентів і весь stdout/stderr verification
dual-agent-task implement --verbose \
  --project /path/to/project tasks/my-feature.md

# Лише фінальний результат або помилка
dual-agent-task implement --quiet \
  --project /path/to/project tasks/my-feature.md
```

Повний stdout/stderr verification і raw-відповіді агентів завжди зберігаються
в `.dual-agent-task/runs/`, незалежно від обраного режиму термінала.

Якщо реалізація вже є у working tree, але попередній запуск перервався на
verification або review, продовжуйте без повторного першого запуску implementer-а:

```bash
dual-agent-task implement \
  --resume-review \
  --project /path/to/project \
  tasks/my-feature.md
```

Перший раунд такого запуску зберігає поточний diff, повторює verification і
починає з read-only review. Codex отримає керування лише якщо reviewer запросить
зміни.

Якщо Claude review завершився, але harness не зміг розібрати structured output,
відновіть той самий run без повторного виклику агентів:

```bash
dual-agent-task recover-review \
  --project /path/to/project \
  /path/to/project/.dual-agent-task/runs/<run>-implement
```

Recovery дозволений лише коли поточний diff побайтово збігається з diff, який
бачив reviewer, і всі verification-логи містять exit code 0.

## Розробка

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`spec` лишається alias для `plan`, щоб старі команди не ламалися.

