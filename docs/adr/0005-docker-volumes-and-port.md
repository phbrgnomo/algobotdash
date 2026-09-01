# Volumes locais e porta do dashboard

O Compose montará `config/` e `data/` do host, com `reports/` opcional para exportações, e exporá o dashboard em `localhost:8765` por padrão. A porta permanece configurável para evitar conflitos com outros serviços locais.

As variáveis locais serão documentadas em `.env.example` e carregadas de `.env`, que permanece fora do Git. Caminhos relativos serão válidos tanto na raiz do projeto no host quanto no diretório `/app` do container.
