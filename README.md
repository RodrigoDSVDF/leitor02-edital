# ÉDITO Pro — Mapeador de Editais

Aplicação Streamlit com interface escura premium, extração de tabelas de provas, leitura de múltiplos cargos, divisão de disciplinas compostas, tópicos hierárquicos com indicação da página, auditoria, editor de provas, planejamento editável e relatórios Word, Excel e PDF.

## Rodar localmente

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Publicar no Streamlit Community Cloud

1. Envie todos os arquivos deste diretório para um repositório GitHub, mantendo a pasta `.streamlit`.
2. No painel de apps do Streamlit, aponte **Main file path** para `app.py`.
3. Configure o projeto como público se quiser que visitantes acessem sem autorização.
4. Não inclua chaves de API em `requirements.txt`, `app.py`, no Git ou no histórico do projeto.

## Principais arquivos

| Arquivo | Função |
|---|---|
| `app.py` | Interface Streamlit, filtros, gráficos, edição e download |
| `styles.css` | Visual escuro responsivo |
| `parser.py` | Extração, estrutura, páginas e auditoria |
| `report_builder.py` | Exportação Word, Excel com checklist e PDF |
| `ai_helper.py` | Parecer opcional via API OpenAI, somente com consentimento |
| `tests/test_pipeline.py` | Testes sintéticos de hierarquia, cargos, pesos e exportações |
| `.streamlit/config.toml` | Tema e limite de upload |

## O que foi melhorado

- Linhas preservadas durante o reconhecimento: o aplicativo não colapsa prematuramente todos os parágrafos.
- Níveis hierárquicos até sete componentes (`1.2.3.4`, etc.).
- Grupos por cargo/perfil, além de Conhecimentos Básicos/Gerais/Específicos.
- Seções romanas em matérias compostas.
- Origem por página (estimada por posição do texto extraído).
- Análise de inconsistências: falta de tabela, faltas de número de itens, divergência com total declarado, cabeçalhos sem classificação, códigos repetidos e tópicos com pais ausentes.
- **Sem percentuais fictícios:** na ausência de números válidos não calcula; prova discursiva não entra no peso objetivo.
- Tabela de prova editável no Streamlit (modificações incluídas na exportação).
- Plano de estudo por tópico, com status e prioridades; exportação do progresso para Excel.
- Excel com folhas `Painel`, `Provas e Pesos`, `Conteúdo Programático`, `Resumo`, `Auditoria` e `Plano de Estudos`.
- DOCX e PDF com seção de auditoria.
- Arquivo temporário é removido automaticamente após o processamento.

## Limitações / cuidados importantes

- O parser é baseado em regras e não garante cobertura universal de todos os modelos de editais; tabelas complexas, colunas e PDFs com linhas quebradas ainda requerem conferência.
- PDFs escaneados exigem OCR **prévio**: esta versão não instala nem executa OCR automaticamente.
- A porcentagem calculada representa **a proporção de itens reconhecidos**, não necessariamente o peso na nota (especialmente quando há valores distintos de pontuação).
- Quando há múltiplos cargos, as linhas da tabela de provas **não são vinculadas automaticamente** ao cargo: confira-as na origem.
- A página de origem é uma estimativa obtida pela posição no texto extraído; valide nos documentos complexos.
- A auditoria descreve a extração original. Após edição manual, confira os avisos novamente.
- A edição do plano fica na sessão do navegador e no Excel exportado, **não** em um banco de dados externo. O progresso não é persistido entre novas sessões.
- A IA opcional só recebe um trecho parcial com consentimento e sua própria chave de API. Ela não modifica automaticamente conteúdo ou pontuação.

## Executar testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests/
```

Os testes usam um edital **sintético** gerado durante a execução; não substituem testes com editais reais de diferentes bancas.

## Atualização v3 — Editais com conteúdo em anexos (exemplo: CNU 2)

**Problema corrigido:** alguns editais principais não apresentam a seção de conteúdo
programático. Eles remetem a anexos em documentos separados. Nesses casos, a
versão anterior interrompia a análise com um erro. Agora o aplicativo:

1. Aceita **vários PDFs ao mesmo tempo** na barra lateral. Envie o edital principal
   e os anexos de seu bloco/cargo em uma única seleção (ou adicione mais PDFs
   pelo mesmo campo do Streamlit).
2. Processa parcialmente o **edital principal**, mesmo sem conteúdo programático.
3. Mostra alertas de **dependências documentais**, sem inventar matérias/tópicos.
4. Preserva a origem dos resultados por arquivo e número de página.
5. Reconhece, no texto corrido do exemplo CNU 2 retificado em 21/11/2025:
   - nível superior: 90 questões (30 de Conhecimentos Gerais e 60 de Conhecimentos Específicos);
   - nível intermediário: 68 questões (sem detalhamento de matérias no arquivo principal);
   - alerta para o fato de que os cinco eixos do nível superior têm 12 questões cada,
     enquanto **pesos de pontuação dependem do cargo e estão nos anexos**.
6. Calcula **participação no número de itens** (33,3% e 66,7% no nível superior),
   mas não confunde esse percentual com o peso na nota final. O total agregado de
   68 questões do nível intermediário **não recebe 100% como se fosse uma matéria**.
7. Exporta o resultado parcial em Word, Excel ou PDF, com avisos de conteúdo ausente.

### Exemplo de uso para o CNU 2

O PDF `edital_concurso-publico-nacional-unificado-2_8aret_21.11.25.pdf`
(56 páginas) **não contém o conteúdo programático dos Blocos 1 a 9**. A
referência aos anexos está no item 8.7, página 28; o quantitativo de questões,
no item 8.9 e 8.10, página 29. Envie também o PDF do(s) anexo(s) do bloco
temático escolhido. Para obter todas as matérias, é necessário dispor dos
anexos correspondentes; não existe extração confiável de conteúdo ausente.

### Notas de confiabilidade

- O reconhecimento narrativo de quantitativos é intencionalmente conservador.
  Não promete funcionar em todo edital sem tabela; pode demandar revisão manual.
- A função `parser.build_reports([(nome_arquivo, caminho), ...])` une diversos PDFs.
  A função histórica `parser.build_report(caminho)` continua funcionando.
- Os testes de integração incluem caso sintético com edital principal + anexo;
  o PDF do CNU 2 foi testado localmente para confirmar o diagnóstico e a
  extração parcial de 30/60/68.
- Se o conteúdo dos anexos estiver em formato pouco padronizado, a ferramenta
  pode retornar trechos não classificados. Confirme a integridade do resultado.


## Atualização V4 — Navegação mais visível

As abas pequenas foram substituídas por **seis botões grandes**, visíveis após o
upload e a análise: **Visão geral**, **Provas e pesos**, **Conteúdo**, **Plano de
estudos**, **Auditoria** e **Exportar**. Cada botão tem ícone, texto explicativo,
foco visível para teclado e estado ativo em violeta. A seção aberta também é
informada logo abaixo da navegação.

As correções feitas na tabela de provas e o progresso do plano de estudos
permanecem na sessão ao navegar para **Exportar**. Nada é salvo em um banco
de dados persistente.

**Para atualizar uma instalação existente**, substitua `app.py` e `styles.css`
pelos arquivos da versão V4. Os demais módulos são compatíveis com a V3.

## Atualização V5 — Suporte multi-banca (Cebraspe, FGV, AOCP, FCC e outras)

O parser foi reescrito a partir da análise de **editais reais de 2025-2026** baixados
dos sites das bancas (PC/AL-Cebraspe, Dataprev-FGV, Seduc-SP-FGV, Unirio). As mudanças
visam o mesmo objetivo: extrair com precisão editais de **qualquer banca**, sem inventar
conteúdo, e sinalizar honestamente o que não pôde ser lido.

### 1. Detecção automática da banca

`parser.detect_banca()` identifica a banca organizadora por marcadores textuais
(Cebraspe, FGV, FCC, AOCP, IBFC, VUNESP, IDECAN, Consulplan, Quadrix, Fundatec, IDIB,
IBADE, Instituto Acesso, Comperve, NUCEPE). A banca aparece na barra lateral, na
Visão geral, na Auditoria e em todos os relatórios exportados (Word, Excel, PDF).

### 2. Formatos de conteúdo programático suportados

| Formato | Bancas típicas | Como é lido |
|---|---|---|
| `DISCIPLINA: 1 tópico. 4.1 subtópico...` inline | Cebraspe, FGV 2026, Unirio, FCC | Tópicos inline após os dois-pontos, inclusive quebrando de linha |
| Tópicos colados (`1.Regime Jurídico...`) | Unirio, editais de governo | Numeração sem espaço aceita |
| Disciplina em linha maiúscula + tópicos em linhas | FGV clássico, IDECAN, Consulplan | Detecção "bare caps" com validação de tópico seguinte |
| Sub-blocos romanos inline (`...: I PARTE 1: ... II PARTE 2: ...`) | Cebraspe | Viram submatérias ligadas à matéria-mãe |
| Itens letrados (`a)`, `itens a)`) | IBFC, VUNESP, FCC | Tópicos de nível inferior |
| Conteúdo programático em tabela (`Disciplina \| Conteúdo`) | AOCP, IDECAN e prefeituras | Fallback: lê as tabelas das páginas do bloco e marca `CONTEUDO_EM_TABELA` |
| Quantitativos narrativos (`...composta de 40 questões de X...`) | Seduc-SP e congêneres | Extrai pares número/disciplina sem criar matérias fictícias |

### 3. Tabelas de provas mais robustas

- **Coluna de cargo**: se a tabela de provas tem coluna "Cargo/Perfil/Especialidade",
  ela é capturada e os percentuais são calculados **por cargo** (não soma cargos
  diferentes); a coluna aparece editável no app e no Excel exportado.
- **Linhas "Total"** não poluem mais o rótulo da prova nem o cargo do bloco seguinte.
- **Tabelas com grade desalinhada** (título mesclado, como o anexo do Dataprev 2026):
  a linha de células antes do cabeçalho vira o rótulo da prova ("Módulo I – ...") e
  linhas com disciplina/itens em colunas deslocadas são recuperadas por varredura.
- **Continuação de tabelas entre páginas**: a tabela quebrada pelo extrator é unida à
  anterior quando não tem cabeçalho próprio, evitando alarmes falsos de divergência.
- **Rótulos normalizados**: `Prova objetiva (P 1)` vira `P1 - Objetiva`.
- Linhas de fase sem disciplina e sem número de itens ("Prova de ...  |  –  |  –") não
  geram mais alertas `ITENS_SEM_NUMERO`.

### 4. Auditoria refinada

- Códigos de tópico **letrados** ("a", "b") repetidos entre pais distintos não são
  mais sinalizados como repetição (comportamento normal desse formato).
- Novo alerta informativo `CONTEUDO_EM_TABELA` quando o conteúdo veio de tabelas.
- O grupo `CONHECIMENTOS ESPECÍFICOS: CARGOS DE NÍVEL ...` agora é reconhecido como
  grupo, e cargos `CARGO/CÓDIGO: 101 – ...`, `PERFIL 1: ...`, `Cargo: ...` e
  `PARA O CARGO DE ...` são todos detectados.

### 5. Validação com editais reais

| Edital (real, 2026) | Resultado |
|---|---|
| Cebraspe — PC/AL nº 1 (71 pág.) | Banca Cebraspe · 21 disciplinas · 300 tópicos · tabela P1/P2/P3 com itens e caráter |
| FGV — Dataprev (63 pág.) | Banca FGV · 73 disciplinas · 1.072 tópicos · 13 perfis/cargos · tabelas por módulo |
| FGV — Seduc-SP (82 pág.) | Banca FGV · quantitativos narrativos 10+30 itens extraídos do texto |
| Unirio — Anexo II (16 pág.) | 25 blocos por cargo · tópicos colados preservados (anexo isolado não traz a banca: sinalizado como não identificada) |

### 6. Testes

`tests/test_pipeline.py` reproduz os formatos das bancas com PDFs sintéticos
(Cebraspe inline+romanos, FGV clássico+tabela desalinhada, Unirio colado, AOCP em
tabela, narrativo, cargo na tabela, mesclagem edital+anexo, detecção de banca,
normalização de rótulos e exportações). Execute:

```bash
python -m pytest -q tests/
```

**Para atualizar uma instalação existente**, substitua `parser.py`, `app.py`,
`report_builder.py` e o README. Nenhuma dependência nova foi adicionada.

## Atualização v5.1 — AOCP, IADES e Quadrix (validados em editais reais)

Três bancas com layout próprio passaram a ter suporte dedicado, implementadas a
partir de editais reais de 2025-2026 baixados dos sites das bancas
(ANPD-IADES, CRA/RJ-IADES, CREF-11/MS-Quadrix, Seduc/RS-AOCP).

### 1. Detecção de banca ampliada

`detect_banca()` reconhece agora também **IADES** (sigla e razão social
"Instituto Americano de Desenvolvimento"). AOCP e Quadrix já eram detectadas;
a AOCP passou a reconhecer também "Instituto AOCP".

### 2. Formato IADES

- Grupos numerados: `1 CONHECIMENTOS PARA TODOS OS CARGOS/OCUPAÇÕES ...`,
  `2 CONHECIMENTOS ESPECÍFICOS`;
- Disciplinas **sem dois-pontos** numeradas com o prefixo do grupo:
  `1.1 Língua Portuguesa e Redação Oficial`, `2.1 ADMINISTRADOR – NÍVEL SUPERIOR
  (CÓDIGO 101)`, incluindo o artefato de extração `1 .2` (espaço entre número e
  ponto) observado no edital da ANPD;
- Tópicos reiniciam em 1 a cada disciplina (inline `1 x. 2 y.` ou colados
  `1.x y.`);
- Tabela "Quadro de Questões e Pesos" com grade desalinhada
  (`Cargo | Prova | Conteúdo | Número de questões | Peso | Total de Pontos |
  Caráter`) — a coluna "Conteúdo" é mapeada como disciplina e o número de
  questões é recuperado por varredura quando a grade está deslocada.

### 3. Formato Quadrix

- Anexo intitulado **"OBJETOS DE AVALIAÇÃO"** e anexos numerados em linha própria
  (`ANEXO IV` sem travessão) fecham corretamente a seção de conteúdo;
- Disciplinas `1.1 LÍNGUA PORTUGUESA: 1 Compreensão de Textos: 1.1 Análise...`
  com subitens hierárquicos inline;
- Títulos de tópicos quebrados de linha (`5 Análise de Dados: 5.1 Estatística...`)
  **não** viram disciplinas falsas: aceita-se disciplina em caixa mista somente
  quando os tópicos reiniciam em 1 e a linha anterior não termina em código de
  tópico pendente;
- Tabela `Fase | Cargo | Prova/Tipo | Área de conhecimento | Número de Itens |
  Caráter`: a coluna "Prova/Tipo" tem prioridade sobre "Fase" na identificação;
  rótulos canonizados (`Prova Objetiva` e `Prova objetiva` viram um único
  `Prova objetiva`).

### 4. Formato AOCP

- Disciplinas colon em caixa mista (`Língua Portuguesa: 1. Leitura...`) com
  reinício de numeração;
- Blocos **"Sugestões de Referências Bibliográficas"** são descartados do
  conteúdo dos tópicos;
- Cargos `CÓDIGO/CARGO: 424 – Professor Artes` viram contexto de cargo;
  linhas `ÁREA 1: LINGUAGENS E SUAS TECNOLOGIAS` viram grupos, e o cargo
  anunciado logo após o cabeçalho da área permanece dentro dela;
- Tabela `Código - Cargo | Fase | Tipo de Prova | Área de Conhecimento | Nº de
  Questões | ... | Caráter`: "Tipo de Prova" vira a prova e "Área de
  Conhecimento" a disciplina; linhas "De acordo com o item 13" não inventam
  matéria — herdam o rótulo da fase (Redação/Títulos).

### 5. Outros refinamentos que beneficiam todas as bancas

- `num_itens` também reconhece cabeçalhos apenas "Número"/"Conteúdo" (grades
  fragmentadas do IADES);
- Cada célula de cabeçalho é usada por uma única coluna (não há mais colisão
  entre "Prova/Tipo" e "Tipo" de outra chave);
- Linhas de fase sem quantitativo (`Títulos | – | –`) não geram mais linhas nem
  alarmes `ITENS_SEM_NUMERO`;
- Códigos pontuados precedidos por espaço (`...técnicos 3.3 Redação...` com ponto
  perdido na extração) são reconhecidos como tópicos, e o dígito final de um
  código pontuado deixa de virar falso tópico `3`;
- Blocos por cargo/área sem cabeçalho de disciplina recebem nomes contextuais
  ("Conteúdo programático específico" / "Área 1: ...") em vez de "Conteúdo não
  classificado";
- A escolha do início da seção de conteúdo passou a considerar disciplinas no
  formato colon e penaliza menções narrativas em minúsculas.

### 6. Validação com editais reais (2025-2026)

| Edital (real) | Resultado |
|---|---|
| IADES — ANPD nº 1/2025 (34 pág.) | Banca IADES · 7 disciplinas · 220 tópicos · quadro de questões e pesos com total conferido · auditoria sem alertas |
| IADES — CRA/RJ nº 1/2026 (27 pág.) | Banca IADES · 9 disciplinas · tabela com 8 linhas por cargo/prova (10+5+5+4+3+3+20 itens, discursiva 20) |
| Quadrix — CREF-11/MS nº 1/2026 (43 pág.) | Banca Quadrix · 12 disciplinas · 493 tópicos · tabela 40+30+50 itens por área |
| AOCP — Seduc/RS professores nº 1/2025 (71 pág.) | Banca AOCP · 27 disciplinas por cargo/área · 16 linhas de prova com cargo, fase e caráter |
| Cebraspe — PC/AL (regressão) | Resultado idêntico ao da v5 (21 disciplinas, 300 tópicos) |
| FGV — Dataprev (regressão) | Tabela de provas preservada (6 linhas); descrições de tópico ("LALUR") não viram mais disciplinas |
| FGV — Seduc-SP (regressão) | Resultado idêntico ao da v5 (Matemática e Língua Portuguesa preservadas) |

### 7. Testes

`tests/test_pipeline.py` ganhou casos sintéticos IADES, Quadrix e AOCP (conteúdo
numerado, objetos de avaliação, bibliografia descartada, coluna Prova/Tipo,
CÓDIGO/CARGO). Execute:

```bash
python -m pytest -q tests/
```

**Para atualizar uma instalação existente**, substitua `parser.py`, `app.py` e
`tests/`. Nenhuma dependência nova foi adicionada.
