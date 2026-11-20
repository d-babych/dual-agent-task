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
tasks/
PROJECT_RULES.md
```

Існуючі файли не перезаписуються. Промпти, схеми й конфігурацію варто комітити;
`.dual-agent-task/runs/` містить локальні журнали запусків.

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

