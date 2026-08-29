# Docker como runtime principal local

O dashboard será executado preferencialmente via Docker, com FastAPI/Uvicorn no container e dados/configuração montados do host. A execução Python direta permanece como fallback de desenvolvimento e diagnóstico, sem ser o caminho operacional documentado como padrão.
