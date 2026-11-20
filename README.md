# dual-agent-task

Локальний CLI-harness, який координує Codex і Claude Code навколо спільних
артефактів: task-файлу, структурованого review, git diff і результатів тестів.

Поточний MVP реалізує перший вертикальний зріз:

- `init` — додає конфігурацію harness у наявний проєкт;
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

Або без встановлення:

```bash
PYTHONPATH=src python3 -m dual_agent_task --help
```

## Ініціалізація робочого проєкту

```bash
dual-agent-task init /path/to/project
dual-agent-task doctor /path/to/project
```

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

## Розробка

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`spec` лишається alias для `plan`, щоб старі команди не ламалися.

