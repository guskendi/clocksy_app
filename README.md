# Clocksy — Controle de Jornada

App web para registro e controle de horas de trabalho, com banco de horas acumulado, justificativas e painel admin.

---

## Stack
- **Backend**: Flask + SQLAlchemy
- **Banco de dados**: PostgreSQL (Neon)
- **Deploy**: Render
- **E-mail**: Resend

---

## Configuração local (desenvolvimento)

```bash
cd clocksy
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edite o .env com suas variáveis

flask db init
flask db migrate -m "initial"
flask db upgrade

flask create-admin
# siga o prompt: informe e-mail, senha e nome do admin

flask run
```

---

## Deploy no Render + Neon

### 1. Banco de dados — Neon

1. Acesse [neon.tech](https://neon.tech) e faça login na sua conta
2. Crie um **novo projeto** (ex: `clocksy`)
3. Copie a **Connection String** no formato:
   ```
   postgresql://user:password@host/dbname?sslmode=require
   ```

### 2. E-mail — Resend

1. Acesse [resend.com](https://resend.com) e faça login
2. Em **API Keys**, crie uma nova chave
3. Configure seu domínio remetente (ou use o domínio de teste do Resend para testes)

### 3. GitHub

1. Crie um repositório novo no GitHub (ex: `clocksy`)
2. Suba o projeto:
   ```bash
   git init
   git add .
   git commit -m "initial commit"
   git remote add origin https://github.com/SEU_USER/clocksy.git
   git push -u origin main
   ```

### 4. Render

1. Acesse [render.com](https://render.com) → **New Web Service**
2. Conecte o repositório `clocksy` do GitHub
3. Configure:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Environment**: Python 3

4. Em **Environment Variables**, adicione:
   ```
   DATABASE_URL      = postgresql://... (string do Neon)
   SECRET_KEY        = uma-chave-aleatoria-longa-aqui
   RESEND_API_KEY    = re_xxxxxxxxxxxx
   RESEND_FROM_EMAIL = noreply@seudominio.com
   APP_URL           = https://clocksy.onrender.com
   ```

5. Clique em **Deploy**

### 5. Criar as tabelas e o admin

Após o primeiro deploy, no terminal do Render (ou localmente apontando para o banco de produção):

```bash
flask db upgrade
flask create-admin
```

Ou via **Render Shell** (aba Shell no painel do serviço):
```bash
flask db upgrade && flask create-admin
```

---

## Funcionalidades

### Usuário comum
- Registro de entrada e saída por dia
- Confirmação do dia (borda verde)
- Justificativas: feriado, emenda, usar horas extras, saída antecipada
- Horas extras descontam do banco (pode ficar negativo)
- Resumo mensal com saldo e banco de horas acumulado
- Configuração de dias e horários por dia da semana
- Reset de senha por e-mail

### Admin
- Login separado (mesma tela, redirecionamento automático)
- Gerenciamento de usuários: criar, editar, ativar/desativar, excluir
- Visualizar registros de qualquer usuário por mês
- Excluir registros individuais
- Cadastro de novos usuários é exclusivo do admin

---

## Estrutura de arquivos

```
clocksy/
├── app.py              # rotas Flask
├── models.py           # models SQLAlchemy
├── utils.py            # cálculos e e-mail
├── requirements.txt
├── Procfile
├── .env.example
└── templates/
    ├── base.html       # layout base + modal + JS
    ├── login.html
    ├── forgot_password.html
    ├── reset_password.html
    ├── registro.html
    ├── resumo.html
    ├── config.html
    └── admin/
        ├── dashboard.html
        ├── user_form.html
        └── user_records.html
```
