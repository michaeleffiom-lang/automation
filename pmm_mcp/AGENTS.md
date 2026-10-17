# Instruções para agentes que modificam este repositório

- Toda modificação no projeto deve incluir uma atualização de `PROMPT.md` na mesma entrega, inclusive alterações em documentação, testes ou configuração.
- Leia `PROMPT.md` e `docs/agent-instructions.md` antes de modificar o projeto. Preserve a correspondência entre ferramentas reais, instruções de conexão, ativação, autenticação e contrato de health check.
- Se uma alteração não afetar o comportamento do agente, registre na revisão do prompt a alteração e por que as instruções operacionais continuam válidas. Não faça mudanças cosméticas apenas para passar no CI.
- `PROMPT.md` é a fonte única do prompt completo; `docs/agent-instructions.md` define como configurar o agente. Atualize o README quando a instalação ou o uso mudarem.
- Nunca versione `.env`, `clients.json`, `.pmm-cache/`, credenciais ou dados reais de clientes. Use exemplos fictícios.
- Execute os testes documentados no README e a verificação de atualização do prompt antes de entregar.
- Use `gh` para operações GitHub; não solicite senha ou token ao usuário. Prefira HTTPS para o remoto.
