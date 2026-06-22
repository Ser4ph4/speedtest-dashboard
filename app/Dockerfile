FROM python:3.11-slim

WORKDIR /app

# Instala as dependências de forma otimizada
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do código do app
COPY . .

EXPOSE 5300

CMD ["python", "app.py"]
