# HashCalculator

Aplicação para **Windows** que calcula o hash de todos os arquivos de uma pasta,
diretório, unidade de disco ou dispositivo de armazenamento e gera um relatório
completo em **Excel (.xlsx)**.

Desenvolvida em **Python 3**, com arquitetura modular, interface gráfica em
**PySide6** e processamento em thread separada — a janela nunca trava durante o
cálculo, mesmo em unidades com milhões de arquivos.

---

## 1. Requisitos

| Item | Versão |
| --- | --- |
| Sistema operacional | Windows 10 ou Windows 11 (32 ou 64 bits) |
| Python | 3.10 ou superior (testado em **3.14.5**) |
| PySide6 | 6.5 ou superior (testado em **6.11.1**) |
| openpyxl | 3.1 ou superior (testado em **3.1.5**) |
| Espaço em disco | ~150 MB para as dependências |
| Memória RAM | 200 MB (independe do tamanho dos arquivos analisados) |

`hashlib`, `sqlite3`, `pathlib`, `logging` e `threading` fazem parte da
biblioteca padrão e não exigem instalação.

> A aplicação também funciona em Linux e macOS para fins de teste, mas os
> recursos específicos (letras de unidade, junction points, caminhos `\\?\`)
> foram projetados e validados para Windows.

---

## 2. Instalação das dependências

Abra o **Prompt de Comando** ou o **PowerShell** na pasta do projeto.

### 2.1. Criar um ambiente virtual (recomendado)

```bash
python -m venv .venv
```

Ative o ambiente:

```bash
.venv\Scripts\activate
```

### 2.2. Instalar as dependências

```bash
pip install -r requirements.txt
```

Para também executar os testes e gerar o `.exe`:

```bash
pip install -r requirements-dev.txt
```

---

## 3. Execução

Com o ambiente virtual ativado:

```bash
python main.py
```

Sem ativar o ambiente virtual:

```bash
.venv\Scripts\python.exe main.py
```

Opções de linha de comando:

```bash
python main.py --version
python main.py --log "C:\Logs\hash_calculator.log"
```

### Passo a passo na interface

1. **Origem** — digite o caminho, clique em `Selecionar...` ou escolha uma das
   unidades detectadas (HD, SSD, pendrive, cartão de memória, unidade de rede).
   A lista é atualizada pelo botão `Atualizar` quando um dispositivo é conectado.
2. **Algoritmos** — `SHA-256` já vem marcado. É possível marcar vários; todos são
   calculados em **uma única leitura** de cada arquivo.
3. **Opções** — tamanho do bloco de leitura (padrão 4 MB), detecção de
   duplicados e se links simbólicos/junctions devem ser seguidos.
4. **Destino** — pasta onde o relatório será gravado. O nome do arquivo é gerado
   automaticamente com data e hora, evitando sobrescrita
   (`Relatorio_Hashes_2026-07-31_143520.xlsx`).
5. **INICIAR ANÁLISE** — acompanhe arquivos encontrados/processados, erros,
   arquivo atual, dados processados, velocidade, tempo decorrido e estimativa de
   conclusão.
6. **CANCELAR** — pede confirmação (*"Deseja realmente interromper a análise?"*),
   encerra com segurança e grava o relatório com os resultados já obtidos.
7. Ao final, a janela de resultado oferece `ABRIR PLANILHA`, `ABRIR PASTA`,
   `NOVA ANÁLISE` e `FECHAR`.

---

## 4. Testes

```bash
pytest
```

Ou, sem ativar o ambiente virtual:

```bash
.venv\Scripts\python.exe -m pytest tests -q
```

São **73 testes**, distribuídos em:

| Arquivo | Cobertura |
| --- | --- |
| `tests/test_hasher.py` | Vetores oficiais de MD5/SHA-1/SHA-256/SHA-512, arquivo vazio, leitura em blocos (1 B a 1 MB), arquivo de 8 MB, nomes Unicode, acesso negado, arquivo inexistente, cancelamento, arquivo alterado/aumentado/reduzido durante o cálculo |
| `tests/test_scanner.py` | Diretórios aninhados, metadados, arquivo vazio, diretório inacessível, cancelamento, junction points, caminhos acima de 260 caracteres, validação da origem |
| `tests/test_engine.py` | Análise completa, conferência dos hashes com `hashlib`, múltiplos algoritmos, duplicados, erro de leitura sem interromper, cancelamento com resultados parciais, pasta vazia, execução em thread, arquivos não modificados |
| `tests/test_excel_report.py` | Estrutura das quatro abas, cabeçalho, congelamento, autofiltro, largura, hash como texto, resumo, erros, duplicados, não sobrescrita, caracteres inválidos |
| `tests/test_gui.py` | Estado inicial, seleção de algoritmos, painel de progresso, bloqueio de controles, validações, análise completa pela interface e **verificação de que os sinais são entregues na thread da GUI** |
| `tests/test_formatter.py` | Formatação de tamanho, duração, números, data/hora, percentual, caminhos longos do Windows |

Verificação independente já realizada: o SHA-256 calculado pela aplicação é
idêntico ao produzido pelo `certutil -hashfile` do Windows.

---

## 5. Geração do executável (.exe)

Com o `pyinstaller` instalado (`pip install -r requirements-dev.txt`):

### 5.1. Forma recomendada — pasta única (`--onedir`)

```bash
pyinstaller HashCalculator.spec --noconfirm --clean
```

O resultado fica em `dist\HashCalculator\HashCalculator.exe` (a pasta inteira
deve ser distribuída, ou compactada em um `.zip`).

Comando equivalente sem o arquivo `.spec`:

```bash
pyinstaller --noconsole --name HashCalculator main.py
```

### 5.2. Arquivo único (`--onefile`)

```bash
pyinstaller --noconsole --onefile --name HashCalculator main.py
```

**Por que `--onedir` é a opção recomendada para PySide6**

O comando `--onefile` funciona, mas apresenta desvantagens concretas com Qt:

* **Inicialização lenta** — a cada execução, o `.exe` descompacta ~90 MB de DLLs
  do Qt em uma pasta temporária (`%TEMP%\_MEIxxxxx`). A abertura passa de
  aproximadamente 2 segundos para 10 a 20 segundos;
* **Antivírus** — executáveis grandes e auto-extraíveis são frequentemente
  bloqueados ou colocados em quarentena, principalmente em redes corporativas;
* **Plugins do Qt** — os plugins de plataforma (`platforms\qwindows.dll`) e de
  estilo são resolvidos por caminho relativo; em `--onefile` eles ficam em uma
  pasta temporária que muda a cada execução, o que ocasionalmente provoca o erro
  *"could not find or load the Qt platform plugin windows"*;
* **Espaço em disco** — a pasta temporária não é sempre removida quando a
  aplicação é encerrada de forma anormal.

O arquivo `HashCalculator.spec` incluído no projeto já exclui os módulos do Qt
que não são utilizados (QtWebEngine, QtQuick, QtMultimedia, Qt3D e outros),
reduzindo o resultado de aproximadamente 250 MB para cerca de 90 MB.

Se o `.exe` de arquivo único for obrigatório, prefira compactar a pasta gerada
pelo `--onedir` em um instalador (Inno Setup ou NSIS) — o resultado é um único
arquivo para o usuário, sem as desvantagens acima.

---

## 6. Funcionamento

```
LOCALIZAR  →  ABRIR SOMENTE PARA LEITURA  →  CALCULAR HASH  →  REGISTRAR
```

### 6.1. Etapas

1. **Varredura** — percorre recursivamente todos os diretórios e subdiretórios
   com `os.scandir`, de forma iterativa (sem recursão), registrando nome,
   extensão, caminho completo, caminho relativo, tamanho e data de modificação.
2. **Cálculo** — cada arquivo é aberto com `open(path, "rb", buffering=0)` e lido
   em blocos de 4 MB. Todos os algoritmos selecionados são alimentados no mesmo
   bloco, portanto o arquivo é lido apenas uma vez.
3. **Relatório** — a planilha é gravada em modo `write_only`, linha a linha.

### 6.2. Consumo de memória

Os resultados **não** são acumulados na memória: cada arquivo processado é
gravado em um banco **SQLite temporário** (criado em `%TEMP%` e removido
automaticamente ao final). Isso permite:

* processar milhões de arquivos com consumo de RAM constante;
* detectar hashes duplicados por agregação SQL, sem carregar todos os hashes;
* preservar os resultados já obtidos quando a análise é cancelada.

### 6.3. Integridade dos dados analisados

* Os arquivos são abertos **exclusivamente em modo binário de leitura**;
* Nada é escrito, renomeado, movido, excluído ou tem atributos/datas alterados;
* Nenhum arquivo analisado é executado ou aberto pelo aplicativo associado do
  Windows. A função de abertura pelo sistema é usada apenas para o **relatório
  gerado** e a pasta que o contém;
* O log registra apenas metadados e resultados — **nunca** o conteúdo dos
  arquivos.

### 6.4. Detecção de alteração durante o cálculo

Antes de ler, a aplicação registra o tamanho e o `mtime` do arquivo; depois de
ler, consulta novamente. Se houver diferença, a linha recebe o status:

```
ATENÇÃO - Arquivo possivelmente alterado durante o cálculo do hash
```

A leitura é limitada ao tamanho observado na abertura. Assim, o hash sempre
corresponde ao arquivo **como ele estava no início do cálculo**, e um arquivo em
crescimento contínuo (um log ativo, por exemplo) não faz a análise entrar em
laço infinito.

### 6.5. Tratamento de erros

Um erro em um arquivo nunca interrompe a análise. A ocorrência é registrada na
coluna `Status` (`ERRO - Acesso negado`, `ERRO - Arquivo em uso por outro
processo`, ...), detalhada na aba `Erros`, e o processamento segue para o
próximo arquivo. São tratados: acesso negado, arquivo bloqueado ou em uso,
arquivo removido durante a análise, caminho excessivamente longo, nome inválido,
erro de E/S, dispositivo desconectado, mídia não pronta, links simbólicos,
junction points e arquivos especiais.

### 6.6. Estrutura do relatório

| Aba | Conteúdo |
| --- | --- |
| **Arquivos** | Nº, Nome, Extensão, Caminho Completo, Caminho Relativo, Tamanho (Bytes), Tamanho Formatado, Algoritmo, Hash, Status — cabeçalho em negrito, primeira linha congelada, autofiltro, larguras ajustadas, hash e caminhos como texto |
| **Resumo** | Origem, início, término, tempo total, algoritmos, bloco de leitura, arquivos encontrados/processados/OK, erros, avisos, ignorados, não processados, diretórios, volume de dados, velocidade média, duplicados, computador, usuário, sistema, versão da aplicação e caminho do log |
| **Erros** | Nº, Arquivo, Caminho, Tipo do erro, Descrição |
| **Hashes Duplicados** | Grupo, Hash, Qtd. no Grupo, Nº no Grupo, Nome, Caminho Completo, Caminho Relativo, Tamanho |

Com **um** algoritmo selecionado, a coluna chama-se `Hash`. Com **vários**, cada
um recebe a sua coluna (`Hash MD5`, `Hash SHA-256`, ...). Os hashes são gravados
em **letras maiúsculas** e por extenso, sem abreviação.

### 6.7. Log

O arquivo `hash_calculator.log` é criado ao lado da aplicação (ou do `.exe`) e
registra início da aplicação, origem selecionada, início da análise, ocorrências,
erros, cancelamento, geração do relatório e conclusão. A rotação é automática
(5 MB, 3 arquivos anteriores). Se a pasta da aplicação não permitir gravação, o
log é redirecionado para a pasta do usuário.

### 6.8. Estrutura do projeto

```
HashCalculator/
├── main.py                    # Ponto de entrada
├── requirements.txt           # Dependências de execução
├── requirements-dev.txt       # Dependências de teste/empacotamento
├── pytest.ini                 # Configuração dos testes
├── HashCalculator.spec        # Especificação do PyInstaller
├── README.md
│
├── gui/
│   ├── __init__.py
│   ├── main_window.py         # Janela principal
│   ├── result_dialog.py       # Janela de resultado final
│   └── worker.py              # QObject/QThread da análise
│
├── core/
│   ├── __init__.py
│   ├── models.py              # Dataclasses e constantes
│   ├── hasher.py              # Cálculo de hash em blocos
│   ├── scanner.py             # Varredura recursiva
│   ├── storage.py             # Armazenamento SQLite temporário
│   └── engine.py              # Orquestração da análise
│
├── reports/
│   ├── __init__.py
│   └── excel_report.py        # Geração do .xlsx
│
├── utils/
│   ├── __init__.py
│   ├── formatter.py           # Formatação pt-BR
│   ├── logger.py              # Log em arquivo com rotação
│   └── paths.py               # Caminhos longos e unidades
│
└── tests/                     # Testes automatizados (pytest)
```

O núcleo (`core/`) não importa Qt: a lógica de análise pode ser reutilizada em
scripts, serviços ou testes sem interface gráfica.

---

## 7. Desempenho medido

Medições reais nesta implementação (Windows 11, Python 3.14, SSD):

| Cenário | Resultado |
| --- | --- |
| Arquivo único de 195 MB, SHA-256 | **1,86 GB/s** (o `certutil` do Windows: 1,23 GB/s) |
| Mesmo arquivo, 4 algoritmos simultâneos | 376 MB/s (uma única leitura do disco) |
| 6.181 arquivos / 667 MB, SHA-256 + MD5 | 25,7 s, relatório de 6.181 linhas gerado em ~1 s |
| Interface durante a análise | bloqueio máximo de **11 ms** por evento |

O tamanho do bloco de leitura tem pouca influência em SSDs (1 MB, 4 MB e 16 MB
apresentaram desempenho equivalente); blocos maiores tendem a ajudar em discos
rígidos e unidades de rede.

---

## 8. Limitações técnicas conhecidas

1. **Antivírus** — a leitura de milhares de arquivos aciona o exame em tempo real
   do Windows Defender, que costuma ser o principal fator limitante em volumes
   com muitos arquivos pequenos (a taxa cai para 25–35 MB/s). Adicionar a origem
   às exclusões acelera bastante a análise.
2. **Limite de linhas do Excel** — uma planilha comporta 1.048.576 linhas. Acima
   disso, o inventário é automaticamente dividido em `Arquivos (2)`,
   `Arquivos (3)`, e assim por diante. As abas `Erros` e `Hashes Duplicados`
   são truncadas nesse mesmo limite.
3. **Arquivos em uso** — arquivos abertos por outro processo com bloqueio
   exclusivo (bancos de dados, `pagefile.sys`, arquivos do sistema em uso) não
   podem ser lidos e são registrados como `ERRO - Arquivo em uso por outro
   processo`. A aplicação não utiliza *Volume Shadow Copy*.
4. **Pastas protegidas do sistema** — executando sem privilégios de
   administrador, diretórios como `C:\System Volume Information` e perfis de
   outros usuários geram `ERRO - Acesso negado`. Executar como administrador
   reduz essas ocorrências, mas ainda assim alguns arquivos permanecem
   inacessíveis.
5. **Links simbólicos e junctions** — não são seguidos por padrão, para evitar
   laços e contagem duplicada. Diretórios de ligação aparecem na aba `Erros`
   como aviso e arquivos de ligação entram no inventário com status
   `IGNORADO`. Ao habilitar a opção de seguir links, há proteção contra laços,
   mas o mesmo arquivo pode ser contabilizado mais de uma vez.
6. **Arquivos duplicados** — o agrupamento usa o **algoritmo principal**
   (o primeiro da ordem MD5 → SHA-1 → SHA-256 → SHA-512 entre os selecionados).
   Todos os arquivos vazios possuem o mesmo hash e, portanto, formam
   legitimamente um grupo de duplicados.
7. **Cancelamento** — os arquivos ainda não processados permanecem no inventário
   com o status `NÃO PROCESSADO - Análise cancelada pelo usuário`. Se o
   cancelamento ocorrer logo após a varredura de um volume muito grande, o
   relatório ainda conterá todas as linhas encontradas.
8. **Precisão do tamanho** — o Windows pode informar um tamanho desatualizado na
   varredura para arquivos que estão sendo gravados no momento. A aplicação
   corrige o valor com o tamanho lido na abertura do arquivo.
9. **Leitura do relatório com openpyxl** — como a planilha é gravada em modo
   `write_only`, ela não contém o elemento opcional de dimensão. Ao reabri-la
   com `openpyxl` em `read_only=True`, `max_row` retorna `None`; percorra as
   linhas com `iter_rows()`. O Excel, o LibreOffice e o Planilhas Google abrem o
   arquivo normalmente.
10. **Discos com falha física** — setores defeituosos podem fazer a leitura
    demorar dezenas de segundos por arquivo (tentativas do driver). O
    cancelamento é verificado entre blocos, portanto pode levar até o fim do
    bloco atual para responder.

---

## 9. Licença e uso

Projeto entregue como aplicação funcional para uso interno. O código está
organizado em módulos independentes e documentado para permitir manutenção e
extensão (por exemplo, inclusão de novos algoritmos em
`core/hasher.py::SUPPORTED_ALGORITHMS`).
