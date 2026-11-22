"""Leitor determinístico e auditável de editais brasileiros de concursos — v5 multi-banca.

Compatível com a API original: build_report(caminho_pdf) -> EditalReport.

Suporte a diferentes formatos de banca (validado em editais reais de 2025-2026):
- Cebraspe: disciplinas com tópicos inline após dois-pontos ("LÍNGUA PORTUGUESA: 1 ...
  4.1 ..."), sub-blocos romanos inline ("I TECNOLOGIA DA INFORMAÇÃO: 1 ...") e tabela
  de provas "PROVA/TIPO | ÁREA | DISCIPLINAS | NÚMERO DE ITENS | CARÁTER";
- FGV: mesmas disciplinas colon + grupos "MÓDULO I - CONHECIMENTOS ... (PARA TODOS OS
  CARGOS/PERFIS)", cargos "PERFIL 1: ...", tabelas com linha-título e coluna Peso;
- IADES: grupos numerados ("1 CONHECIMENTOS PARA TODOS OS CARGOS/OCUPAÇÕES ...") e
  disciplinas "1.1 Língua Portuguesa e Redação Oficial" sem dois-pontos, com tópicos
  que reiniciam em 1 na linha seguinte; tabela "Quadro de Questões e Pesos" com grade
  desalinhada ("Cargo | Prova | Conteúdo | Número de questões | Peso | Caráter");
- Quadrix: anexo "OBJETOS DE AVALIAÇÃO" com disciplinas "1.1 LÍNGUA PORTUGUESA: 1 ..."
  e subitens hierárquicos inline; tabela "Fase | Cargo | Prova/Tipo | Área de
  conhecimento | Número de Itens | Caráter";
- AOCP: disciplinas colon em caixa mista ("Língua Portuguesa: 1. Leitura...") com
  blocos "Sugestões de Referências Bibliográficas" descartados, cargos "CÓDIGO/CARGO:
  424 – Professor Artes" e tabelas "Código - Cargo | Fase | Tipo de Prova | Área de
  Conhecimento | Nº de Questões | ... | Caráter";
- Unirio e congêneres: tópicos colados ao número ("1.Regime Jurídico...");
- Formato clássico (FGV antigo, IDECAN, Consulplan): disciplina em linha própria
  maiúscula seguida de tópicos numerados em linhas;
- Conteúdo programático publicado em tabelas ("Disciplina | Conteúdo") — fallback;
- Quantitativos narrativos no corpo do edital ("... 30 questões de X e 70 de Y ...").

O parser não inventa conteúdo nem percentuais: preserva a proveniência (página e arquivo)
e registra alertas quando o documento não puder ser interpretado com segurança.
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd
import pdfplumber

PHASE_COLUMNS = ["prova", "materia", "num_itens", "carater", "pagina", "tabela_id"]


def _norm(s: object) -> str:
    value = str(s or "")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value).strip().upper()


def _clean(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


# ---------------------------------------------------------------------------
# Detecção de banca — marcadores curtos no início do documento, nomes completos
# em qualquer parte (anexos podem não trazer cabeçalho da banca).
# ---------------------------------------------------------------------------
_BANCA_MARKERS: list[tuple[str, str]] = [
    ("Cebraspe", r"CEBRASPE"),
    ("FGV", r"\bFGV\b|FUNDACAO GETULIO VARGAS"),
    ("FCC", r"\bFCC\b"),
    ("IADES", r"\bIADES\b"),
    ("AOCP", r"\bAOCP\b"),
    ("IBFC", r"\bIBFC\b"),
    ("VUNESP", r"\bVUNESP\b"),
    ("IDEcan", r"\bIDE\s?CAN\b"),
    ("Consulplan", r"\bCONSULPLAN\b"),
    ("Quadrix", r"\bQUADRIX\b"),
    ("Fundatec", r"\bFUNDATEC\b"),
    ("IDIB", r"\bIDIB\b"),
    ("IBADE", r"\bIBADE\b"),
    ("Instituto Acesso", r"INSTITUTO\s+ACESSO"),
    ("Comperve", r"\bCOMPERVE\b"),
    ("NUCEPE", r"\bNUCEPE\b"),
]
_BANCA_MARKERS_FULL: list[tuple[str, str]] = [
    ("Cebraspe", r"CENTRO\s+DE\s+SELECAO\s+E\s+(?:DE\s+)?PROMOCAO\s+DE\s+EVENTOS"),
    ("FGV", r"FUNDACAO\s+GETULIO\s+VARGAS"),
    ("FCC", r"FUNDACAO\s+CARLOS\s+CHAGAS"),
    ("IADES", r"INSTITUTO\s+AMERICANO\s+DE\s+DESENVOLVIMENTO"),
    ("AOCP", r"ASSOCIACAO\s+DE\s+APOIO\s+AOS?\s+SERVIDORES\s+DO\s+PODER\s+JUDICIARIO|INSTITUTO\s+AOCP"),
    ("IBFC", r"INSTITUTO\s+BRASILEIRO\s+DE\s+FORMACAO\s+E\s+CAPACITACAO"),
    ("VUNESP", r"FUNDACAO\s+PARA\s+O\s+VESTIBULAR"),
    ("IDEcan", r"INSTITUTO\s+DE\s+ESTUDOS\s+E\s+DESENVOLVIMENTO\s+CIDADAO"),
    ("IDIB", r"INSTITUTO\s+DE\s+DESENVOLVIMENTO\s+INSTITUCIONAL\s+BRASIL"),
]


def detect_banca(full_text: str) -> str:
    """Identifica a banca organizadora por marcadores textuais (página 1-2 primeiro)."""
    head = _norm(full_text[:4500])
    body = _norm(full_text)
    for corpus in (head, body):
        patterns = _BANCA_MARKERS if corpus is head else _BANCA_MARKERS_FULL
        best, best_pos = "Não identificada", len(corpus) + 1
        for name, pat in patterns:
            m = re.search(pat, corpus)
            if m and m.start() < best_pos:
                best, best_pos = name, m.start()
        if best != "Não identificada":
            return best
    return "Não identificada"


@dataclass
class Topico:
    codigo: str
    nivel: int
    texto: str
    pagina: int | None = None
    arquivo: str = ""


@dataclass
class Materia:
    grupo: str
    nome: str
    texto: str
    topicos: list[Topico]
    materia_mae: str | None = None
    subsecoes: list[tuple[str, list[Topico]]] | None = None
    cargo: str = "Geral"
    pagina: int | None = None
    arquivo: str = ""
    origem: str = "texto"  # texto | tabela


@dataclass
class AuditIssue:
    severidade: str  # alta / media / informativa
    codigo: str
    mensagem: str
    pagina: int | None = None


@dataclass
class EditalRaw:
    full_text: str
    pages_text: list[str] = field(default_factory=list)
    tables_by_page: dict[int, list[list[list[str | None]]]] = field(default_factory=dict)
    page_offsets: list[int] = field(default_factory=list)

    def page_at(self, offset: int) -> int | None:
        if not self.page_offsets:
            return None
        return max(1, bisect.bisect_right(self.page_offsets, offset))


@dataclass
class EditalReport:
    phase_table: pd.DataFrame
    materias: list[Materia]
    section_preview: str
    section_text: str
    issues: list[AuditIssue] = field(default_factory=list)
    paginas: int = 0
    arquivo: str = ""
    banca: str = "Não identificada"
    totais_declarados: dict[str, int] = field(default_factory=dict)
    documentos: list[str] = field(default_factory=list)
    documentos_pendentes: list[str] = field(default_factory=list)
    notas_provas: list[str] = field(default_factory=list)

    @property
    def cargos(self) -> list[str]:
        return sorted({m.cargo for m in self.materias})


def _clean_page(text: str, page_number: int) -> str:
    """Mantém quebras de linha (essenciais para distinguir cabeçalhos)."""
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    lines = text.splitlines()
    # Remove apenas número igual à página nas extremidades, nunca no corpo do texto.
    for idx in range(min(2, len(lines))):
        if lines[idx].strip() == str(page_number):
            lines[idx] = ""
    for idx in range(max(0, len(lines) - 2), len(lines)):
        if lines[idx].strip() == str(page_number):
            lines[idx] = ""
    return "\n".join(lines)


def extract_raw(pdf_path: str | Path) -> EditalRaw:
    pages: list[str] = []
    tables: dict[int, list] = {}
    offsets: list[int] = []
    pos = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            offsets.append(pos)
            content = _clean_page(page.extract_text(x_tolerance=2, y_tolerance=3) or "", i + 1)
            pages.append(content)
            pos += len(content) + 1
            # Detecção de tabelas pode falhar em PDFs com células desenhadas como texto.
            try:
                found = page.extract_tables() or []
            except Exception:
                found = []
            if found:
                tables[i] = found
    return EditalRaw("\n".join(pages), pages, tables, offsets)


# ---------------------------------------------------------------------------
# Tabela de provas e quantitativos
# ---------------------------------------------------------------------------
_HEADER_KEYWORDS = {
    "prova": ("PROVA", "TIPO DE PROVA", "AVALIACAO", "FASE", "ETAPA"),
    "materia": ("DISCIPLINA", "MATERIA", "AREA DE CONHECIMENTO", "CONHECIMENTO", "COMPONENTE", "BLOCO", "TIPO", "CONTEUDO"),
    "num_itens": ("NUMERO DE ITENS", "N DE ITENS", "Nº DE ITENS", "ITENS", "QUESTOES", "QUANTIDADE DE QUESTOES", "QTD", "NUMERO"),
    "carater": ("CARATER", "ELIMINATORIO", "CLASSIFICATORIO"),
    "cargo": ("CARGO", "PERFIL", "ESPECIALIDADE"),
}


def _header_mapping(row: list) -> dict[str, int]:
    """Mapeia colunas do cabeçalho. Cada célula é usada por no máximo uma chave e a
    coluna "Prova/Tipo" tem prioridade sobre "Fase"/"Etapa" (ex.: Quadrix)."""
    names = [_norm(c) for c in row]
    mapping: dict[str, int] = {}
    used: set[int] = set()

    def find(key: str, choices: tuple[str, ...]) -> None:
        if key in mapping:
            return
        for idx, name in enumerate(names):
            if idx in used or not name:
                continue
            if any(choice in name for choice in choices):
                mapping[key] = idx
                used.add(idx)
                return

    find("prova", ("PROVA", "TIPO DE PROVA", "AVALIACAO"))
    find("materia", _HEADER_KEYWORDS["materia"])
    find("num_itens", _HEADER_KEYWORDS["num_itens"])
    find("carater", _HEADER_KEYWORDS["carater"])
    find("cargo", _HEADER_KEYWORDS["cargo"])
    find("prova", ("FASE", "ETAPA"))
    return mapping


def _number(text: object) -> int | None:
    value = _clean(text)
    if not value or value in {"-", "—", "–"}:
        return None
    m = re.fullmatch(r"(\d{1,4})(?:\s*(?:itens|quest[oõ]es))?", value, flags=re.I)
    return int(m.group(1)) if m else None


_KINDS_PROVA = [
    ("capacidade f[íi]sica", "Capacidade física"),
    ("biopsicossocial", "Biopsicossocial"),
    ("objetiva", "Objetiva"),
    ("discursiva", "Discursiva"),
    ("pr[áa]tica", "Prática"),
    ("t[íi]tulos", "Títulos"),
    ("digita[çc][ãa]o", "Digitação"),
    ("psicol[óo]gica", "Psicológica"),
    ("f[íi]sica", "Física"),
    ("m[ée]dic[oa]", "Médica"),
]


def _label_prova(s: str) -> str:
    """Normaliza rótulos como "Prova objetiva (P 1)" em "P1 - Objetiva"."""
    s = _clean(s)
    if not s:
        return s
    num = re.search(r"\bP\s*[\(\[]?\s*\)?\s*(\d+)\b", s, flags=re.I)
    kind = None
    for pat, label in _KINDS_PROVA:
        if re.search(rf"\b{pat}", s, flags=re.I):
            kind = label
            break
    if num and kind:
        # Texto restante que traga informação útil (ex.: "de Conhecimentos Básicos").
        extra = re.sub(rf"(?i)\b{kind.split()[0]}\w*\b", " ", s)
        extra = re.sub(r"(?i)\bprovas?\b|\bP\s*[\(\[]?\s*\)?\s*\d+\b", " ", extra)
        extra = re.sub(r"[()\[\]/,;.:\-–—]", " ", extra)
        extra = re.sub(r"\b(?:de|da|do|das|tipo|nº|no)\b", " ", extra, flags=re.I)
        extra = re.sub(r"\s+", " ", extra).strip(" -–")
        return f"P{num.group(1)} - {kind}" + (f" - {extra}" if extra else "")
    if num:
        return f"P{num.group(1)}"
    return s


# Bancas escrevem o mesmo rótulo com capitalizações diferentes ("Prova objetiva" /
# "Prova Objetiva"); sem canonicalização, a interface agruparia fases duplicadas.
_CANON_PROVA = {
    "PROVA OBJETIVA": "Prova objetiva",
    "PROVA DISCURSIVA": "Prova discursiva",
    "PROVA PRATICA": "Prova prática",
    "PROVA DE TITULOS": "Prova de títulos",
    "AVALIACAO DE TITULOS": "Avaliação de títulos",
    "TITULOS": "Títulos",
    "PROVA DE REDACAO": "Prova de redação",
    "REDACAO": "Redação",
    "PROVA FISICA": "Prova física",
    "PROVA PSICOLOGICA": "Prova psicológica",
    "AVALIACAO PSICOLOGICA": "Avaliação psicológica",
    "PROVA OBJETIVA DE CONHECIMENTOS GERAIS": "Prova objetiva - Conhecimentos Gerais",
    "PROVA OBJETIVA DE CONHECIMENTOS ESPECIFICOS": "Prova objetiva - Conhecimentos Específicos",
}


def _canonical_prova(label: str) -> str:
    norm = _norm(label)
    return _CANON_PROVA.get(norm, _clean(label))


def _plausible_materia(cell_text: object) -> bool:
    """Célula plausível de ser disciplina: texto com letras, não Total/cabeçalho."""
    t = _clean(cell_text)
    if len(t) < 3 or not re.search(r"[A-Za-zÀ-ü]", t):
        return False
    norm = _norm(t)
    if norm in {"DISCIPLINA", "MATERIA", "TOTAL"} or "TOTAL" in norm or "Nº DE" in norm or "N DE" in norm:
        return False
    if _norm(t) in {"DISCIPLINA", "MATERIA", "AREA DE CONHECIMENTO", "CONHECIMENTO", "TIPO"}:
        return False
    # Células explicativas de fases sem disciplina (AOCP: "De acordo com o item 13").
    if re.match(r"^(?:DE\s+ACORDO|CONFORME)\b", norm):
        return False
    return True


def _row_plausible(raw_row: list, mapping: dict[str, int]) -> bool:
    """Linha plausível de continuação: disciplina preenchida + número de itens.

    Exige número na coluna de itens: sem isso, cabeçalhos de tabelas vizinhas
    ("Cod | Cargo | AC | PCD | PN ...") seriam capturados como continuação."""
    def cell(key: str) -> str:
        at = mapping.get(key, -1)
        return _clean(raw_row[at] if 0 <= at < len(raw_row) else "")
    materia = cell("materia")
    if not materia or len(materia) < 3:
        return False
    norm = _norm(materia)
    if norm in {"DISCIPLINA", "MATERIA", "TOTAL"} or "TOTAL" in norm:
        return False
    return _number(cell("num_itens")) is not None


def extract_phase_table(raw: EditalRaw) -> tuple[pd.DataFrame, dict[str, int]]:
    rows: list[dict] = []
    totals: dict[str, int] = {}
    table_counter = 0
    # Estado de continuação: tabelas grandes costumam ser quebradas pelo pdfplumber
    # entre páginas; sem isso, o "Total" final cairia em outra tabela e geraria
    # alarme falso de divergência.
    prev_mapping: dict[str, int] | None = None
    prev_table_id = None
    prev_last_prova = "Prova objetiva"
    prev_last_carater = ""
    prev_last_cargo = ""
    prev_page = -10
    for page_index in sorted(raw.tables_by_page.keys()):
        for table in raw.tables_by_page[page_index]:
            if not table:
                continue
            mapping: dict[str, int] = {}
            header_index = -1
            title = ""
            for i, candidate in enumerate(table[:5]):
                proposed = _header_mapping(candidate)
                if {"materia", "num_itens"}.issubset(proposed):
                    mapping, header_index = proposed, i
                    break
            if header_index < 0:
                if (prev_mapping is not None and page_index - prev_page <= 1
                        and any(_row_plausible(r, prev_mapping) for r in table[:2])):
                    mapping = prev_mapping
                    table_id = prev_table_id
                    last_prova, last_carater, last_cargo = prev_last_prova, prev_last_carater, prev_last_cargo
                else:
                    continue
            else:
                table_counter += 1
                table_id = table_counter
                # Linhas antes do cabeçalho carregam o título do módulo/fase.
                title_cells = []
                for candidate in table[:header_index]:
                    for c in candidate:
                        c = _clean(c)
                        if c and len(c) >= 8 and re.search(r"[A-Za-zÀ-ü]", c) and not re.fullmatch(r"[\d\s.,%-]+", c):
                            title_cells.append(c)
                title = _clean(" ".join(title_cells))[:90]
                last_prova = title if title else "Prova objetiva"
                last_carater = ""
                last_cargo = ""
                prev_mapping, prev_table_id, prev_page = mapping, table_id, page_index
                prev_last_prova, prev_last_carater, prev_last_cargo = last_prova, last_carater, last_cargo
            for raw_row in table[(header_index + 1) if header_index >= 0 else 0:]:
                if not raw_row:
                    continue
                def cell(key: str) -> str:
                    at = mapping.get(key, -1)
                    return _clean(raw_row[at] if 0 <= at < len(raw_row) else "")
                prova, materia, count, carater = cell("prova"), cell("materia"), cell("num_itens"), cell("carater")
                cargo_cell = cell("cargo")
                materia_clean = _clean(materia)
                materia_is_dash = materia_clean in {"-", "—", "–"}
                # Linhas "Total" não atualizam rótulos de prova/cargo do bloco seguinte.
                if prova and "TOTAL" not in _norm(prova):
                    last_prova = _canonical_prova(_label_prova(prova))
                if carater:
                    last_carater = carater
                if cargo_cell and "TOTAL" not in _norm(cargo_cell):
                    last_cargo = cargo_cell
                # Tabelas com grade desalinhada (título mesclado, ex.: FGV e IADES): se a
                # coluna mapeada vier vazia, procura números na própria linha — mas não em
                # linhas de fase com travessão ("Redação | – | –").
                if not count and not materia_is_dash:
                    for cval in raw_row:
                        if re.fullmatch(r"\d{1,4}", _clean(cval)):
                            count = _clean(cval)
                            break
                count_number = _number(count)
                if "TOTAL" in _norm(materia):
                    if count_number is not None:
                        totals[f"{table_id}|{last_prova}|{last_cargo}"] = count_number
                    continue
                if not materia and count_number is not None and any(
                        re.match(r"(?i)\s*total", str(c)) for c in raw_row if c):
                    totals[f"{table_id}|{last_prova}|{last_cargo}"] = count_number
                    continue
                if count_number is not None and not _plausible_materia(materia):
                    if materia_clean and not materia_is_dash:
                        # Fase sem disciplina ("Redação | De acordo com o item 13 | 1"):
                        # herda o rótulo da prova em vez de inventar matéria.
                        materia = last_prova
                    else:
                        for cval in raw_row:
                            txt = _clean(cval)
                            if not txt or not re.search(r"[A-Za-zÀ-ü]", txt):
                                continue
                            if re.match(r"(?i)^\s*(?:prova|total)\b", txt):
                                continue
                            if _plausible_materia(txt):
                                materia = txt
                                break
                # Linhas de fases sem quantitativo ("Prova de títulos | – | –" ou célula
                # vazia) carregam só o caráter; não geram linha nem alarme de auditoria.
                if count_number is None and re.fullmatch(r"[-–—.\s]*", _clean(count) or ""):
                    continue
                if not materia_clean and count_number is None:
                    continue
                if _norm(materia) in {"DISCIPLINA", "MATERIA", "AREA DE CONHECIMENTO", "CONHECIMENTO", "TIPO"}:
                    continue
                if not count and not carater:
                    continue
                row = {"prova": last_prova, "materia": materia,
                       "num_itens": count_number if count_number is not None else count,
                       "carater": last_carater, "pagina": page_index + 1, "tabela_id": table_id}
                if "cargo" in mapping or last_cargo:
                    row["cargo"] = last_cargo
                rows.append(row)
                prev_last_prova, prev_last_carater, prev_last_cargo = last_prova, last_carater, last_cargo
            # Continuações atualizam a prova caráter/cargo para a próxima página.
            prev_page = page_index
    return pd.DataFrame(rows, columns=PHASE_COLUMNS + (["cargo"] if any("cargo" in r for r in rows) else [])), totals


# ---------------------------------------------------------------------------
# Localização da seção de conteúdo programático
# ---------------------------------------------------------------------------
_START = [
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:DOS?\s+)?OBJETOS?\s+DE\s+AVALIA[ÇC][ÃA]O\b", re.I | re.M),
    re.compile(r"^\s*(?:ANEXO\s+[IVXLCDM\d]+\s*[-–—:]?\s*)?(?:DOS?\s+)?CONTE[ÚU]DOS?\s+PROGRAM[ÁA]TICOS?\b", re.I | re.M),
    re.compile(r"^\s*(?:ANEXO\s+[IVXLCDM\d]+\s*[-–—:]?\s*)?(?:DOS?\s+)?PROGRAMAS?\s+DAS?\s+PROVAS\b", re.I | re.M),
]
_END = [
    re.compile(
        r"^\s*ANEXO\s+[IVXLCDM\d]+\s*[-–—:]\s*"
        r"(?!((?:DOS?|DA|DO|DE)\s+)?(?:CONTE[ÚU]D?OS?|OBJETOS?|PROGRAMA))\S",
        re.I | re.M,
    ),
    # Bancas como a Quadrix publicam o número do anexo em linha própria ("ANEXO IV"),
    # sem travessão; encerra quando o título da linha seguinte não é de conteúdo.
    re.compile(
        r"^\s*ANEXO\s+[IVXLCDM\d]+\s*\r?\n\s*"
        r"(?!((?:DOS?|DA|DO|DE)\s+)?(?:CONTE[ÚU]D?OS?|OBJETOS?|PROGRAMA))\S",
        re.I | re.M,
    ),
    re.compile(r"^\s*(?:DOS?\s+RECURSOS?|DAS?\s+DISPOSI[ÇC][ÕO]ES\s+(?:FINAIS|GERAIS)|DA\s+HOMOLOGA[ÇC][ÃA]O)\b", re.I | re.M),
    re.compile(r"^\s*(?:DOS?\s+)?CRONOGRAMA\b", re.I | re.M),
    re.compile(r"^\s*QUADRO\s+DE\s+(?:VAGAS|PROVAS|REMUNERA|PONTUA)", re.I | re.M),
]


def find_section(full_text: str) -> tuple[int, int, str]:
    candidates: dict[int, int] = {}
    for pat in _START:
        for m in pat.finditer(full_text):
            candidates[m.start()] = m.end()
    if not candidates:
        raise ValueError("Não foi localizada uma seção identificável como 'Conteúdo programático' ou 'Objetos de avaliação'.")
    # Prefere candidato que possua disciplinas e numeração logo depois do cabeçalho.
    # Disciplinas colon pesam mais que numeração solta e menções narrativas em
    # minúsculas ("...os conteúdos programáticos, aos critérios...") são penalizadas.
    def rank(pair: tuple[int, int]) -> tuple[int, int]:
        start, end = pair
        sample = full_text[end:end + 6000]
        header_count = len(re.findall(r"(?m)^\s*[A-ZÀ-Ü][A-ZÀ-Ü /,&()-]{4,70}:\s*", sample))
        topics = len(re.findall(r"(?m)^\s*\d+(?:\.\d+)*\s+[A-ZÀ-Üa-zà-ü]", sample))
        colon = len(_find_subjects_colon(sample))
        score = header_count * 4 + colon * 8 + topics
        matched = full_text[start:end]
        if not re.search(r"[A-ZÀ-Ü]", matched):
            score //= 4
        return (score, start)
    start, _ = max(candidates.items(), key=rank)
    # O encerramento é o primeiro cabeçalho terminal válido depois de 300 caracteres.
    ends = [m.start() for pattern in _END for m in pattern.finditer(full_text, start + 300)]
    end = min(ends) if ends else len(full_text)
    if end <= start:
        end = len(full_text)
    return start, end, full_text[start:end]


# ---------------------------------------------------------------------------
# Contextos: cargos e grupos de conhecimento
# ---------------------------------------------------------------------------
_CARGO_RES = [
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?(?:CARGO|PERFIL)\s*(?:N?[ºo°]?\s*)?(\d+)\s*[:\-–—]\s*(.{3,130})\s*$", re.I),
    re.compile(r"^\s*CARGO\s*[\/\-–—]\s*C[ÓO]DIGO\s*[:\-–—]?\s*(?:N?[ºo°]?\s*)?(\d+)?\s*[-–—]?\s*(.{3,130})\s*$", re.I),
    # AOCP publica cargos como "CÓDIGO/CARGO: 424 – Professor Artes".
    re.compile(r"^\s*C[ÓO]DIGO\s*[\/\-–—]\s*CARGO\s*[:\-–—]?\s*(?:N?[ºo°]?\s*)?(\d+)?\s*[-–—]?\s*(.{3,130})\s*$", re.I),
    re.compile(r"^\s*(?:CARGO|PERFIL)\s*[:\-–—]\s*(.{3,130})\s*$", re.I),
    re.compile(r"^\s*PARA\s+(?:O\s+CARGO\s+DE\s+|CARGO\s*(?:\/\s*C[ÓO]DIGO)?\s*[:\-–—]?\s*)(.{3,130})\s*$", re.I),
    re.compile(r"^\s*CARGO\s+DE\s+(?!N[ÍI]VEL\b)(.{3,130})\s*$", re.I),
]
_GROUP_RES = [
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?CONHECIMENTOS\s+(?:B[ÁA]SICOS|ESPEC[ÍI]FICOS|GERAIS|COMUNS)(?:\s+(?:COMUNS|PARA|DOS?|DA|DO|DE|AOS?|AO|NAS?|NO)\b.{0,90})?\s*:?\s*$", re.I),
    re.compile(r"^\s*CONHECIMENTOS\s+(?:B[ÁA]SICOS|ESPEC[ÍI]FICOS|GERAIS)\s*:\s*(?:CARGOS?\s+DE\s+N[ÍI]VEL\s+.{0,60}|PARA\s+.{0,80})\s*$", re.I),
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?PROVA\s+DE\s+CONHECIMENTOS\s+(?:B[ÁA]SICOS|ESPEC[ÍI]FICOS|GERAIS)\s*:?\s*$", re.I),
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?M[ÓO]DULO\s+(?:[IVXLCDM]+|\d+|[A-Z])\s*(?:[-–—:]?\s*.{3,110})?\s*:?\s*$", re.I),
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?EIXO\s+TEM[ÁA]TICO\s+(?:\d+|[IVXLCDM]+)\s*(?:[-–—:]?\s*.{3,110})?\s*:?\s*$", re.I),
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?BLOCO\s+(?:\d+|[IVXLCDM]+|[A-Z])\s*(?:[-–—:]?\s*.{3,110})?\s*:?\s*$", re.I),
    re.compile(r"^\s*(?:\d+(?:\.\d+)*\s+)?[ÁA]REA\s+(?:TEM[ÁA]TICA|DE\s+CONHECIMENTO|TECNOL[ÓO]GICA)\s*\d*\s*(?:[-–—:]?\s*.{3,110})?\s*:?\s*$", re.I),
    # AOCP divide o conteúdo por área: "ÁREA 1: LINGUAGENS E SUAS TECNOLOGIAS".
    re.compile(r"^\s*[ÁA]REA\s+\d{1,3}\s*[:\-–—]\s*\S.{3,110}\s*$", re.I),
    re.compile(r"^\s*CARGOS?\s+DE\s+N[ÍI]VEL\s+[A-ZÀ-Üa-zà-ü/ ]{3,60}\s*:?\s*$", re.I),
    # IADES/Quadrix numeram os grupos: "1 CONHECIMENTOS PARA TODOS OS CARGOS/OCUPAÇÕES
    # ..." e "2 CONHECIMENTOS COMPLEMENTARES".
    re.compile(r"^\s*\d{1,3}\s+CONHECIMENTOS\s+\S.{0,110}\s*$", re.I),
]


def _cargo_label(prefix: str, num: str | None, name: str) -> str:
    name = _clean(name).strip(" :-–—")
    return f"{prefix} {num} — {name}" if num else f"{prefix} — {name}"


def _group_label(stripped: str) -> str:
    label = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", stripped).strip(" :")
    label = label.title()
    for word in (" De ", " Do ", " Da ", " Dos ", " Das ", " E ", " Em ", " Para ", " Os ", " As ", " A ", " O "):
        label = label.replace(word, word.lower())
    return label


def _split_contexts(section_text: str) -> list[tuple[str, str, str, int]]:
    """Retorna (cargo, grupo, texto_do_bloco, offset_relativo_no_section_text)."""
    blocks: list[tuple[str, str, str, int]] = []
    cargo = "Geral"
    grupo = "Conteúdo Programático"
    last_content_start = 0
    pos = 0
    for line in section_text.splitlines(keepends=True):
        stripped = line.strip()
        cargo_hit = None
        for rx in _CARGO_RES:
            m = rx.fullmatch(stripped)
            if m:
                cargo_hit = m
                break
        group_hit = None
        if cargo_hit is None:
            for rx in _GROUP_RES:
                m = rx.fullmatch(stripped)
                if m:
                    group_hit = m
                    break
        if cargo_hit or group_hit:
            if section_text[last_content_start:pos].strip():
                blocks.append((cargo, grupo, section_text[last_content_start:pos], last_content_start))
            if cargo_hit:
                groups = cargo_hit.groups()
                num = groups[0] if groups and groups[0] and str(groups[0]).isdigit() else None
                nome = groups[-1] or ""
                norm_line = _norm(stripped)
                prefix = "Perfil" if norm_line.startswith("PERFIL") else "Cargo"
                cargo = _cargo_label(prefix, num, nome)
                # Cargo anunciado logo após um cabeçalho de grupo (AOCP: "ÁREA 1: ..."
                # seguido de "CÓDIGO/CARGO: ...") permanece dentro daquele grupo.
                if section_text[last_content_start:pos].strip() or grupo == "Conteúdo Programático":
                    grupo = "Conteúdo Programático"
            else:
                grupo = _group_label(stripped)
            last_content_start = pos + len(line)
        pos += len(line)
    if section_text[last_content_start:].strip():
        blocks.append((cargo, grupo, section_text[last_content_start:], last_content_start))
    return blocks or [("Geral", "Conteúdo Programático", section_text, 0)]


# ---------------------------------------------------------------------------
# Disciplinas: formatos colon (Cebraspe/FGV), bare caps (clássico) e tabelas
# ---------------------------------------------------------------------------
_SUBJECT_RE = re.compile(
    r"^\s*(?:(?P<npfx>\d{1,3}(?:\.\d{1,3}){0,5})\s+)?"
    r"(?P<name>[A-ZÀ-Ü][A-Za-zÀ-ü0-9 /,()&º°ª+'’\-–.]{3,100}?)\s*:\s*(?P<tail>.*)$"
)
_BARE_RE = re.compile(r"^\s*(?P<name>[A-ZÀ-Ü][A-ZÀ-Ü0-9 /,()&º°ª+\-–.'’]{3,90})\s*$")
_SUBJECT_BLOCKLIST = {
    "CONHECIMENTOS", "CONHECIMENTOS BASICOS", "CONHECIMENTOS ESPECIFICOS", "CONHECIMENTOS GERAIS",
    "CONHECIMENTOS COMUNS", "OBJETOS DE AVALIACAO", "CONTEUDO PROGRAMATICO", "CONTEUDOS PROGRAMATICOS",
    "PROVA OBJETIVA", "PROVA DISCURSIVA", "PROVA PRATICA", "PROVA DE TITULOS", "HABILIDADES",
    "DISCIPLINA", "MATERIA", "AREA DE CONHECIMENTO", "CONHECIMENTO", "COMPONENTE", "BLOCO",
    "DESCRICAO", "EMENTA", "ASSUNTO", "TOPICO", "CODIGO", "CARGO", "PERFIL", "PROVA", "FASE",
    "ETAPA", "TABELA", "QUADRO", "LEGENDA", "CRONOGRAMA", "BIBLIOGRAFIA", "FONTE", "FONTES",
    "NOTA", "NOTAS", "OBSERVACAO", "ATENCAO", "AVISO", "IMPORTANTE", "INSTRUCOES", "INSTRUCAO",
    "SUMARIO", "RETIFICACAO",
    # AOCP: anexos de requisitos por cargo e áreas ("CÓDIGO/CARGO: 424 – Professor...").
    "CODIGO/CARGO", "CODIGO-CARGO", "HABILITACAO", "REQUISITOS", "ATRIBUICOES", "AREA",
    "SUGESTOES DE REFERENCIAS BIBLIOGRAFICAS", "SUGESTOES DE REFERENCIA BIBLIOGRAFICA",
}
_SUBJECT_PREFIXES = ("CARGO", "PERFIL", "ANEXO", "TOTAL", "SECAO", "SEÇÃO", "TABELA", "QUADRO",
                     "CRONOGRAMA", "BIBLIOGRAFIA", "BANCA", "PROVA OBJETIVA", "PROVA DISCURSIVA",
                     "PROVA PRÁTICA", "PROVA PRATICA", "PROVA DE TÍTULOS", "PROVA DE TITULOS")


def _is_subject(name: str) -> bool:
    norm = _norm(name)
    if norm in _SUBJECT_BLOCKLIST or norm.startswith(_SUBJECT_PREFIXES):
        return False
    if norm.startswith("CONHECIMENTOS ") and len(norm.split()) >= 2:
        if norm.split()[1] in {"BASICOS", "GERAIS", "ESPECIFICOS", "COMUNS"}:
            return False
    if re.match(r"^(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\s+", norm):
        return False
    # Nomes que carregam numeração de tópicos quebrados de linha ("...na\nAdministração
    # contemporânea. 2.FUNÇÕES E PRÁTICAS...") não são disciplinas.
    if re.search(r"\d\.[A-ZÀ-Ü]", name) or re.search(r"[.!?]\s+\d", name):
        return False
    if len(name.split()) > 14 or len(name) > 100 or re.search(r"[.!?]\s+[A-ZÀ-Ü]", name):
        return False
    if name.count("(") != name.count(")"):
        return False
    return len(name) >= 4 and any(c.isalpha() for c in name)


_QUICK_TOPIC_RE = re.compile(
    r"^\s*(?:\d{1,3}(?:\.\d{1,3}){0,6}\)?\.?\s+[\wÀ-ü(\"“]"
    r"|\d{1,3}\.?[A-Za-zÀ-ü]"
    r"|itens?\s+[a-z]\)"
    r"|[a-z]\)\s+[\wÀ-ü(\"“])"
)
_TAIL_TOPIC_RE = re.compile(r"^\s*(?:\d{1,3}(?:\.\d{1,3}){0,6}\)?\.?\s*(?=[\wÀ-ü(\"“])|(?:itens?\s+)?[a-z]\)\s*)")


def _next_nonempty(text: str, pos: int) -> tuple[int | None, str]:
    idx = text.find("\n", pos)
    while idx != -1:
        rest = text[idx + 1:]
        stripped = rest.lstrip()
        if stripped:
            return idx + 1 + (len(rest) - len(stripped)), stripped
        idx = text.find("\n", idx + 1)
    return None, ""


_TAIL_CODE_RE = re.compile(r"^\s*(?P<num>\d{1,3}(?:\.\d{1,3}){0,6})\)?\.?")
_TAIL_LETTER_RE = re.compile(r"^\s*(?:itens?\s+)?(?P<let>[a-z])\)")
# Reinício real de numeração: "1 Texto", "1. Texto", "1.Texto" ou "1) Texto" —
# mas não "1.3 Cidadania" (subtópico/disciplina que continua a numeração). Aceita
# também reinício letrado "a) Texto".
_RESTART_ONE_RE = re.compile(r"^\s*(?:1(?:\s|\.(?!\d)|\))|[a-z]\)\s)")


def _tail_first_code(tail: str) -> str | None:
    """Primeiro código de tópico do trecho ("1", "1.", "5.1", "a)") ou None."""
    m = _TAIL_CODE_RE.match(tail)
    if m:
        return m.group("num")
    m = _TAIL_LETTER_RE.match(tail)
    if m:
        return m.group("let")
    return None


def _restarts_at_one(line: str) -> bool:
    """A linha inicia uma sequência de tópicos reiniciando em 1 (ou itens "a)")."""
    return _RESTART_ONE_RE.match(line) is not None


def _prev_dangles_code(text: str, line_start: int) -> bool:
    """A linha anterior termina em código de tópico pendente ("... básicos. 2.5"), sem
    pontuação final — sinal de que a linha atual é continuação da descrição daquele
    tópico, não um cabeçalho de disciplina. Um código com ponto ("... versão 1.1.")
    fecha a frase e não é pendente."""
    chunk = text[max(0, line_start - 400):line_start]
    prev_lines = [ln for ln in chunk.split("\n") if ln.strip()]
    if not prev_lines:
        return False
    prev = prev_lines[-1].rstrip()
    return bool(re.search(r"(?:^|\s)\d{1,3}(?:\.\d{1,3}){0,6}\)?\s*$", prev))


def _find_subjects_colon(text: str) -> list[tuple[int, int, str]]:
    """Disciplinas no formato "NOME: tópicos..." (Cebraspe, FGV, Unirio, FCC, AOCP,
    Quadrix). Nomes em caixa alta são aceitos diretamente; em caixa mista exigem
    tópicos reiniciando em 1 — evita que títulos de tópicos quebrados de linha
    ("2 Análise de Dados: 5.1 Estatística...") virem disciplinas falsas."""
    found: list[tuple[int, int, str]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        m = _SUBJECT_RE.match(line.rstrip("\r\n"))
        if m:
            name = _clean(m.group("name"))
            if _is_subject(name):
                caps = bool(re.fullmatch(r"[A-ZÀ-Ü0-9 /,()&º°ª+\-–.'’]+", name))
                ok = caps
                if not ok:
                    npfx = m.group("npfx")
                    code = _tail_first_code(m.group("tail"))
                    if code is not None and re.fullmatch(r"1\.?", code):
                        ok = True  # tópicos reiniciam em 1 (AOCP, Unirio, Quadrix...)
                    elif code is not None and re.fullmatch(r"[a-z]", code):
                        ok = True  # itens letrados ("a)") reiniciam por natureza
                    elif npfx and code is not None and code.startswith(npfx + "."):
                        ok = False  # continuação do próprio prefixo ("1 Título: 1.1...")
                    else:
                        # Sem código no tail: aceita se a linha seguinte inicia um tópico
                        # e a linha anterior não termina em código pendente (seduc-SP:
                        # "Matemática: Conteúdos Programáticos" + "1.1. Sistema...").
                        npos, nline = _next_nonempty(text, cursor + len(line) - 1)
                        next_topic = npos is not None and _QUICK_TOPIC_RE.match(nline)
                        ok = bool(next_topic and not _prev_dangles_code(text, cursor))
                elif caps:
                    if _prev_dangles_code(text, cursor):
                        ok = False
                    else:
                        code = _tail_first_code(m.group("tail"))
                        if code is None:
                            npos, nline = _next_nonempty(text, cursor + len(line) - 1)
                            has_restart = npos is not None and _restarts_at_one(nline)
                            # Siglas curtas de uma palavra sem tópicos ("BNCC: progressões...")
                            # são continuação de frase, não disciplina.
                            if not has_restart and len(name.split()) == 1 and len(name) <= 6:
                                ok = False
                if ok:
                    found.append((cursor, cursor + m.start("tail"), name))
        cursor += len(line)
    return found


# Disciplinas numeradas sem dois-pontos (IADES): grupo "1 CONHECIMENTOS ..." e
# disciplinas "1.1 Língua Portuguesa e Redação Oficial" / "2.1 ADMINISTRADOR –
# NÍVEL SUPERIOR (CÓDIGO 101)" com tópicos que reiniciam em 1 na linha seguinte.
# Aceita o artefato de extração "1 .2" (espaço entre número e ponto, ANPD 2025).
_NUMBERED_SUBJECT_RE = re.compile(
    r"^\s*(?P<pfx>\d{1,3})\s*\.\s*(?P<sub>\d{1,3})\.?\s+(?P<name>[A-ZÀ-Üa-zà-ü][A-Za-zÀ-ü0-9 /,()&º°ª+'’\-–.]{3,100}?)\s*$"
)


def _find_subjects_numbered(text: str) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        m = _NUMBERED_SUBJECT_RE.match(line.rstrip("\r\n"))
        if m:
            name = _clean(m.group("name"))
            norm = _norm(name)
            if len(name) >= 4 and norm not in _SUBJECT_BLOCKLIST and not norm.startswith(_SUBJECT_PREFIXES):
                npos, nline = _next_nonempty(text, cursor + len(line) - 1)
                if npos is not None and _restarts_at_one(nline):
                    found.append((cursor, cursor + m.end(), name))
        cursor += len(line)
    return found


def _line_kind(line: str) -> tuple[str, str]:
    s = line.strip()
    if not s:
        return "blank", ""
    if _QUICK_TOPIC_RE.match(s):
        return "topic", ""
    m = _BARE_RE.match(s)
    if m and _is_subject(m.group("name")):
        return "cand", _clean(m.group("name"))
    return "other", ""


def _find_subjects_bare(text: str) -> list[tuple[int, int, str]]:
    """Disciplinas em linha maiúscula própria, seguidas de tópicos (formato clássico)."""
    entries: list[list] = []  # [line_index, start, end, name]
    cursor = 0
    for li, line in enumerate(text.splitlines(keepends=True)):
        m = _BARE_RE.match(line.rstrip("\r\n"))
        if m and _is_subject(m.group("name")):
            entries.append([li, cursor, cursor + m.start("name") + len(m.group("name")), _clean(m.group("name"))])
        cursor += len(line)
    next_info = []
    for e in entries:
        next_info.append(_next_nonempty(text, e[2] - 1))
    accepted: set[int] = set()
    for i in range(len(entries) - 1, -1, -1):
        npos, nline = next_info[i]
        if npos is None:
            continue
        kind, _ = _line_kind(nline)
        if kind == "topic":
            accepted.add(i)
        elif kind == "cand":
            j = next((k for k, e in enumerate(entries) if e[1] == npos), None)
            if j is not None and j > i and j in accepted:
                accepted.add(i)
    return [(e[1], e[2], e[3]) for i, e in enumerate(entries) if i in accepted]


# ---------------------------------------------------------------------------
# Tópicos: numéricos (linha e inline), colados e letrados
# ---------------------------------------------------------------------------
_TOPIC_LINE_RE = re.compile(r"(?m)^[ \t]*(?P<code>\d{1,3}(?:\.\d{1,3}){0,6})\)?\.?[ \t]+(?=[\wÀ-ü(\"“])")
# Códigos inline após ponto/dois-pontos ou no início do trecho. O (?<![.\d]) evita
# capturar o dígito final de um código pontuado ("3.3" → falso tópico "3").
_TOPIC_INLINE_RE = re.compile(
    r"(?:(?<=[.;:])|^)\s*(?<![.\d])(?P<code>\d{1,3}(?:\.\d{1,3}){0,6})\.?\s*(?=[A-ZÀ-Ü(\"“])"
)
# Bancas (IADES/ANPD) às vezes perdem o ponto na extração ("...técnicos 3.3 Redação
# ..."); códigos pontuados precedidos por espaço são tópicos inequívocos.
_TOPIC_INLINE_DOT_RE = re.compile(
    r"(?<=[\s(])\s*(?P<code>\d{1,3}\.\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+(?=[A-ZÀ-Ü(\"“])"
)
_TOPIC_LETTER_RE = re.compile(r"(?m)^[ \t]*(?:itens?\s+)?(?P<code>[a-z])\)\s*(?=[\wÀ-ü(\"“])")
_PREFACE_SKIP_RE = re.compile(r"^\d{1,3}\s*(?:\([^)]{1,30}\))?\s*(?:itens?|quest[õo]es)?\)?\.?\s*$", re.I)


def _collect_topic_matches(content: str) -> list[re.Match]:
    cands: list[tuple[int, int, re.Match]] = []
    for order, rx in enumerate((_TOPIC_LINE_RE, _TOPIC_INLINE_RE, _TOPIC_INLINE_DOT_RE, _TOPIC_LETTER_RE)):
        for m in rx.finditer(content):
            cands.append((m.start("code"), order, m))
    cands.sort(key=lambda t: (t[0], t[1]))
    out: list[re.Match] = []
    seen: set[int] = set()
    for cs, _, m in cands:
        if cs in seen:
            continue
        seen.add(cs)
        out.append(m)
    out.sort(key=lambda m: m.start())
    return out


def extract_topics_hierarchical(content: str, locate: Callable[[int], int | None] | None = None) -> list[Topico]:
    """Extrai até sete níveis, com tópicos inline ("A: 1 x. 2 y."), colados ("1.z") e letrados."""
    if not content or not content.strip():
        return []
    matches = _collect_topic_matches(content)
    if not matches:
        return [Topico("", 0, _clean(content), locate(0) if locate else None)]
    topics: list[Topico] = []
    preface = _clean(content[:matches[0].start()])
    if preface and not _PREFACE_SKIP_RE.fullmatch(preface):
        topics.append(Topico("", 0, preface, locate(0) if locate else None))
    last_num_nivel = None
    for i, m in enumerate(matches):
        code = m.group("code")
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        description = _clean(content[m.end():next_start])
        if not description:
            continue
        if code.isdigit():
            nivel = code.count(".") + 1
            last_num_nivel = nivel
        else:
            nivel = (last_num_nivel + 1) if last_num_nivel else 1
        topics.append(Topico(code, nivel, description, locate(m.start()) if locate else None))
    return topics


# Sub-blocos romanos inline: "DISCIPLINA: I SUB1: 1 ... II SUB2: 1 ..." (Cebraspe).
_ROMAN_INLINE_RE = re.compile(
    r"(?<![A-Za-zÀ-ü0-9])"
    r"(?P<roman>XIX|XVIII|XVII|XVI|XIV|XIII|XII|XI|X|IX|VIII|VII|VI|IV|III|II|I)\s+"
    r"(?P<name>[A-ZÀ-Ü][A-Za-zÀ-ü0-9 ,()&/'’\-–.]{2,70}?):\s*(?=\d{1,3}[\s.])"
)


def _split_romans(content: str) -> list[tuple[str, str, int]]:
    hits: list[tuple[int, int, str]] = []
    for m in _ROMAN_INLINE_RE.finditer(content):
        name = _clean(m.group("name"))
        if len(name) < 4 or _norm(name) in _SUBJECT_BLOCKLIST:
            continue
        hits.append((m.start(), m.end(), name))
    if len(hits) < 2 or hits[0][0] > 150:
        return []
    if len({_norm(n) for _, _, n in hits}) != len(hits):
        return []
    result: list[tuple[str, str, int]] = []
    for i, (_, after_header, name) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(content)
        result.append((name, content[after_header:end], after_header))
    return result


# ---------------------------------------------------------------------------
# Fallback: conteúdo programático publicado em tabelas ("Disciplina | Conteúdo")
# ---------------------------------------------------------------------------
_CONTENT_TABLE_KEYWORDS = {
    "materia": ("DISCIPLINA", "MATERIA", "AREA DE CONHECIMENTO", "COMPONENTE", "UNIDADE"),
    "topico": ("CONTEUDO", "ASSUNTO", "TOPICO", "DESCRICAO", "EMENTA", "PROGRAMA", "HABILIDADE"),
    "cargo": ("CARGO", "PERFIL", "ESPECIALIDADE"),
}


def _content_header_mapping(table: list) -> tuple[int | None, dict | None]:
    for i, row in enumerate(table[:5]):
        if not row:
            continue
        mapping: dict[str, int] = {}
        for idx, cell in enumerate(row):
            name = _norm(cell)
            if not name:
                continue
            for key, choices in _CONTENT_TABLE_KEYWORDS.items():
                if key not in mapping and any(choice in name for choice in choices):
                    mapping[key] = idx
                    break
        if "materia" in mapping and "topico" in mapping and mapping["materia"] != mapping["topico"]:
            return i, mapping
    return None, None


def _subjects_from_tables(raw: EditalRaw, start_offset: int, end_offset: int,
                          cargo: str, grupo: str) -> list[Materia]:
    p1 = raw.page_at(start_offset)
    p2 = raw.page_at(max(end_offset - 1, start_offset))
    if p1 is None:
        return []
    collected: dict[tuple[str, str], Materia] = {}
    order: list[tuple[str, str]] = []
    cargo_atual = cargo
    for page in range(p1, (p2 or p1) + 1):
        for table in raw.tables_by_page.get(page - 1, []):
            if not table:
                continue
            header_idx, mapping = _content_header_mapping(table)
            if mapping is None:
                continue
            def cell(row, key):
                at = mapping.get(key, -1)
                return _clean(row[at] if 0 <= at < len(row) else "")
            current_key = None
            for row in table[header_idx + 1:]:
                if not row:
                    continue
                cargo_cell = cell(row, "cargo")
                if cargo_cell:
                    cargo_atual = cargo_cell
                materia_cell = cell(row, "materia")
                norm_m = _norm(materia_cell)
                if norm_m and ("TOTAL" in norm_m or norm_m in {"DISCIPLINA", "MATERIA"}):
                    continue
                topico_cell = cell(row, "topico")
                if materia_cell:
                    current_key = (cargo_atual, norm_m or materia_cell)
                    if current_key not in collected:
                        collected[current_key] = Materia(grupo, materia_cell, "", [],
                                                         cargo=cargo_atual, pagina=page, origem="tabela")
                        order.append(current_key)
                if not topico_cell or current_key is None:
                    continue
                m = collected[current_key]
                topics = extract_topics_hierarchical(topico_cell)
                if not topics:
                    topics = [Topico("", 1, _clean(topico_cell), page)]
                for t in topics:
                    if not t.codigo and t.nivel == 0:
                        t.nivel = 1
                    if t.pagina is None:
                        t.pagina = page
                m.topicos.extend(topics)
                m.texto = _clean(m.texto + " " + topico_cell)[:4000]
    return [collected[k] for k in order]


# AOCP (e outras bancas) intercala "Sugestões de Referências Bibliográficas" entre
# as disciplinas; a bibliografia não faz parte do conteúdo a estudar.
_BIBLIO_MARK_RE = re.compile(
    r"(?mi)^\s*SUGEST[ÕO]E?S\s+DE\s+REFER[ÊE]NCI[AE]S?\s+BIBLIOGR[ÁA]FICAS?\s*:?\s*$"
)


def _cut_bibliografia(content: str) -> str:
    m = _BIBLIO_MARK_RE.search(content)
    return content[:m.start()] if m else content


def _parse_block(cargo: str, grupo: str, text: str, offset: int, raw: EditalRaw, section_offset: int) -> list[Materia]:
    headers = _find_subjects_colon(text)
    if not headers:
        headers = _find_subjects_numbered(text)
    if not headers:
        headers = _find_subjects_bare(text)
    output: list[Materia] = []
    if not headers:
        # Não transforme o próprio título da seção em disciplina fictícia: se o bloco
        # não tem conteúdo próprio além do título do anexo, nem tente as tabelas.
        noncontent = re.sub(r"(?im)^\s*(?:ANEXO\s+[IVXLCDM\d]+\s*[-–—:]?\s*)?(?:CONTE[ÚU]DO\s+PROGRAM[ÁA]TICO|OBJETOS?\s+DE\s+AVALIA[ÇC][ÃA]O)\s*$", "", text)
        noncontent = _cut_bibliografia(noncontent)
        if not noncontent.strip():
            return []
        tabelas = _subjects_from_tables(raw, section_offset + offset,
                                        section_offset + offset + len(text), cargo, grupo)
        if tabelas:
            return tabelas
        # Evita descartar conteúdos de formato fora do padrão: usuário pode revisar.
        topicos = extract_topics_hierarchical(noncontent, lambda at: raw.page_at(section_offset + offset + at))
        if not topicos:
            return []
        if grupo and grupo != "Conteúdo Programático":
            nome_fallback = grupo  # ex.: "Área 1: Linguagens e Suas Tecnologias" (AOCP)
        elif cargo and cargo != "Geral":
            nome_fallback = "Conteúdo programático específico"
        else:
            nome_fallback = "Conteúdo não classificado"
        return [Materia(grupo, nome_fallback, _clean(noncontent), topicos,
                        cargo=cargo, pagina=raw.page_at(section_offset + offset))]
    for idx, (start, after_header, nome) in enumerate(headers):
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(text)
        content = _cut_bibliografia(text[after_header:end])
        location_base = section_offset + offset + after_header
        roman_parts = _split_romans(content)
        if roman_parts:
            for sub_name, sub_content, sub_off in roman_parts:
                topics = extract_topics_hierarchical(sub_content, lambda at, n=sub_off: raw.page_at(location_base + n + at))
                if topics:
                    output.append(Materia(grupo, sub_name, _clean(sub_content), topics,
                                          materia_mae=nome, cargo=cargo,
                                          pagina=raw.page_at(location_base + sub_off)))
        else:
            topics = extract_topics_hierarchical(content, lambda at: raw.page_at(location_base + at))
            if topics:
                output.append(Materia(grupo, nome, _clean(content), topics, cargo=cargo,
                                      pagina=raw.page_at(section_offset + offset + start)))
    return output


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------
def _validate(report: EditalReport, raw: EditalRaw) -> None:
    issues = report.issues
    if not report.phase_table.empty:
        df = report.phase_table.copy()
        df["valor"] = pd.to_numeric(df["num_itens"], errors="coerce")
        group_cols = ["tabela_id", "prova"]
        has_cargo = "cargo" in df.columns and df["cargo"].astype(str).str.strip().ne("").any()
        if has_cargo:
            group_cols.append("cargo")
        for key, group in df.groupby(group_cols, dropna=False):
            prova = key[1]
            cargo_part = str(key[2]) if has_cargo and len(key) > 2 and pd.notna(key[2]) else ""
            total_key = f"{key[0]}|{prova}|{cargo_part}"
            known = int(group["valor"].sum())
            no_number = int(group["valor"].isna().sum())
            if no_number:
                issues.append(AuditIssue("media", "ITENS_SEM_NUMERO", f"{prova}: {no_number} linha(s) sem número de itens verificável."))
            declared = report.totais_declarados.get(total_key)
            if declared is not None and known != declared:
                issues.append(AuditIssue("alta", "TOTAL_DIVERGENTE", f"{prova}: foram extraídos {known} itens, mas a tabela declara {declared}."))
            elif declared is None and not re.search(r"discursiv|reda[çc][ãa]o|t[ií]tulos|pr[aá]tic|teste f[ií]sico|taf|texto", str(prova), re.I):
                issues.append(AuditIssue("informativa", "TOTAL_NAO_DECLARADO", f"{prova}: total utilizado no gráfico ({known}) é calculado pelas linhas reconhecidas; confira o edital."))
    else:
        issues.append(AuditIssue("alta", "SEM_TABELA", "Tabela de provas e itens não reconhecida. O conteúdo pode ser mapeado, mas os pesos não foram calculados."))
    if not report.materias:
        issues.append(AuditIssue("alta", "SEM_MATERIAS", "Nenhuma matéria foi identificada no conteúdo programático."))
    for m in report.materias:
        if m.nome == "Conteúdo não classificado":
            issues.append(AuditIssue("media", "SEM_CABECALHO", f"Trecho de {m.cargo} / {m.grupo} não pôde ser classificado por disciplina.", m.pagina))
        numbered = [t for t in m.topicos if t.codigo]
        # Códigos letrados ("a", "b"...) repetem-se entre pais distintos: normal.
        numeric_codes = [t for t in numbered if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){0,6}", t.codigo)]
        codes = {t.codigo for t in numeric_codes}
        if not numbered:
            issues.append(AuditIssue("media", "SEM_NUMERACAO", f"{m.nome}: tópicos sem numeração reconhecida; revisão recomendada.", m.pagina))
        elif len(codes) != len(numeric_codes):
            issues.append(AuditIssue("media", "CODIGOS_REPETIDOS", f"{m.nome}: há códigos de tópico repetidos.", m.pagina))
        for topic in numbered:
            if topic.nivel > 1:
                parent = topic.codigo.rsplit(".", 1)[0]
                if parent not in codes:
                    issues.append(AuditIssue("informativa", "PAI_AUSENTE", f"{m.nome}: tópico {topic.codigo} não tem o item pai {parent} reconhecido.", topic.pagina))
                    break
    # Páginas com pouco texto são uma indicação, não uma confirmação, de PDF escaneado.
    thin_pages = [i + 1 for i, txt in enumerate(raw.pages_text) if len(txt.strip()) < 40]
    if thin_pages:
        issues.append(AuditIssue("media", "PAGINAS_POUCO_TEXTO", f"{len(thin_pages)} página(s) sem texto pesquisável suficiente: {', '.join(map(str, thin_pages[:12]))}. Podem ser imagens ou páginas em branco."))


# ---------------------------------------------------------------------------
# Quantitativos narrativos no corpo do edital
# ---------------------------------------------------------------------------
def _provas_narrativas(raw: EditalRaw) -> tuple[pd.DataFrame, list[str]]:
    """Reconhece quantitativos expressos em parágrafos, sem criar matérias fictícias.

    Abrange a redação encontrada no edital principal do CPNU2; outros layouts
    continuam dependendo de regras de extração adicionais ou revisão manual.
    """
    texto = raw.full_text
    horizontal = re.sub(r"\s+", " ", texto)
    rows: list[dict] = []
    notas: list[str] = []

    def pagina_para(trecho: str) -> int | None:
        m = re.search(re.escape(trecho), texto, re.I)
        return raw.page_at(m.start()) if m else None

    # Não confunde distribuição de questões com os pesos da pontuação.
    sup = re.search(
        r"Para os cargos de N[íi]vel Superior, a Prova Objetiva será composta por\s*"
        r"(\d+)\s*\([^)]*\)\s*questões,\s*sendo\s*"
        r"(\d+)\s*\([^)]*\)\s*questões de Conhecimentos Gerais\s+e\s*"
        r"(\d+)\s*\([^)]*\)\s*questões de Conhecimentos Específicos",
        horizontal, re.I,
    )
    if sup:
        total, gerais, especificos = (int(x) for x in sup.groups())
        if total == gerais + especificos and total > 0:
            page = pagina_para("8.9.")
            for materia, itens in [("Conhecimentos Gerais", gerais), ("Conhecimentos Específicos", especificos)]:
                rows.append({"prova": "Nível Superior — Prova objetiva", "materia": materia,
                             "num_itens": itens, "carater": "Eliminatório e classificatório",
                             "pagina": page, "tabela_id": 9001, "escopo": "bloco", "origem_tipo": "texto"})
            notas.append(f"Nível superior: {total} questões = {gerais} gerais + {especificos} específicas (item 8.9).")
            if re.search(r"5\s*\(cinco\)\s*eixos temáticos, cada\s+um contendo\s*12\s*\(doze\)\s*questões", horizontal, re.I):
                notas.append("As 60 questões específicas são distribuídas em 5 eixos de 12 questões; os pesos na pontuação dependem do cargo/especialidade (itens 8.9.1 a 8.9.3).")

    inter = re.search(
        r"Para os cargos de N[íi]vel Intermediário, a Prova Objetiva será composta por\s*(\d+)\s*\([^)]*\)\s*questões",
        horizontal, re.I,
    )
    if inter:
        total = int(inter.group(1))
        if total > 0:
            rows.append({"prova": "Nível Intermediário — Prova objetiva", "materia": "Total — disciplinas especificadas nos anexos",
                         "num_itens": total, "carater": "Eliminatório e classificatório",
                         "pagina": pagina_para("8.10."), "tabela_id": 9002,
                         "escopo": "total", "origem_tipo": "texto"})
            notas.append(f"Nível intermediário: {total} questões; distribuição por matéria requer os anexos VIII e IX (item 8.10).")

    return pd.DataFrame(rows, columns=PHASE_COLUMNS + ["escopo", "origem_tipo"]), notas


_NARR_MARKER_RE = re.compile(
    r"(\d{1,3})\s*(?:\([^)]{1,40}\))?\s*(?:quest[õo]es|itens)?\s*(?:ser[ãa]o?\s+|ser[áa]\s+)?"
    r"(?:relativ[oa]s?\s+a[oa]s?\s+|de\s+|em\s+)",
    re.I,
)
_NARR_BLOCKLIST = {"MULTIPLA ESCOLHA", "ESCOLHA", "VERDADEIRO OU FALSO", "CERTO OU ERRADO",
                   "RESPOSTA UNICA", "ESTUDO DE CASO", "CADA ALTERNATIVA"}


def _provas_narrativas_generico(raw: EditalRaw) -> tuple[pd.DataFrame, list[str]]:
    """Genérico: "…composta de 40 questões de X, 30 de Y e 30 de Z" / "10 serão relativas aos..."."""
    texto = raw.full_text
    rows: list[dict] = []
    notas: list[str] = []
    tid = 9200
    for seg_m in re.finditer(r"[^.;!?]{60,1200}[.;!?]", texto):
        seg = seg_m.group(0)
        if not re.search(r"quest[õo]es|itens", seg, re.I):
            continue
        if not re.search(r"composta|compor[áa]|relativ[oa]s?|distribu[íi]|constitu[íi]", seg, re.I):
            continue
        matches = list(_NARR_MARKER_RE.finditer(seg))
        if len(matches) < 2:
            continue
        pares: list[tuple[int, str]] = []
        for i, m in enumerate(matches):
            start = m.end()
            stop = matches[i + 1].start() if i + 1 < len(matches) else len(seg)
            nome = seg[start:stop]
            nome = re.sub(r"\s+(?:e|respectivamente|sendo|cada|conforme)\s*$", "", nome, flags=re.I)
            nome = re.sub(r",\s*(?:conforme|de acordo|respectivamente|cada|no total|devendo)\b.*$", "", nome, flags=re.I)
            nome = re.sub(r"\s*\([^)]{0,40}\)\s*$", "", nome)
            nome = re.sub(r"\s+", " ", nome).strip(" ,;:-–")
            if not nome or re.match(r"^\d", nome):
                continue
            norm = _norm(nome)
            if norm in _NARR_BLOCKLIST or norm.startswith(("PROVA", "BANCA", "TOTAL")) or len(nome.split()) > 9:
                continue
            pares.append((int(m.group(1)), nome))
        if len(pares) < 2:
            continue
        label = "Prova objetiva (texto)" if re.search(r"objetiva", seg, re.I) else "Prova (texto)"
        if re.search(r"discursiva", seg, re.I) and not re.search(r"objetiva", seg, re.I):
            label = "Prova discursiva (texto)"
        carater = ""
        if re.search(r"eliminat[óo]ri", seg, re.I) and re.search(r"classificat[óo]ri", seg, re.I):
            carater = "Eliminatório e classificatório"
        elif re.search(r"eliminat[óo]ri", seg, re.I):
            carater = "Eliminatório"
        elif re.search(r"classificat[óo]ri", seg, re.I):
            carater = "Classificatório"
        page = raw.page_at(seg_m.start())
        escopo_base = "bloco" if all(_norm(n).startswith(("CONHECIMENTOS", "BLOCO", "MÓDULO", "MODULO", "EIXO")) for _, n in pares) else "disciplina"
        for num, nome in pares:
            rows.append({"prova": label, "materia": nome, "num_itens": num, "carater": carater,
                         "pagina": page, "tabela_id": tid, "escopo": escopo_base, "origem_tipo": "texto"})
        total = sum(n for n, _ in pares)
        notas.append("Quantitativos narrativos: " + ", ".join(f"{n} de {nome}" for n, nome in pares) + f" (total {total}).")
        tid += 1
    return pd.DataFrame(rows, columns=PHASE_COLUMNS + ["escopo", "origem_tipo"]), notas


def _documentos_complementares(text: str) -> list[str]:
    """Aponta dependências textuais explícitas, nunca supõe conteúdo ausente."""
    norm = _norm(text)
    if re.search(r"CONTEUDOS? PROGRAMATICOS?.{0,110}ANEXOS? I A IX", norm) or re.search(
        r"CONTEUDOS? PROGRAMATICOS? APRESENTADOS NOS ANEXOS? I A IX", norm
    ):
        return ["Anexos I a IX — conteúdos programáticos e distribuição por bloco/cargo"]
    if re.search(r"CONTEUDOS? PROGRAMATICOS?.{0,110}ANEXOS?", norm):
        return ["Anexos citados no edital — conferir e enviar os PDFs contendo o conteúdo programático"]
    return []


# ---------------------------------------------------------------------------
# Montagem final
# ---------------------------------------------------------------------------
def build_reports(documents: list[tuple[str, str | Path]]) -> EditalReport:
    """Une vários PDFs, preservando a identificação de origem de cada extração."""
    if not documents:
        raise ValueError("Envie ao menos um arquivo PDF.")
    reports: list[EditalReport] = []
    for nome, caminho in documents:
        result = build_report(caminho)
        result.arquivo = nome
        result.documentos = [nome]
        for m in result.materias:
            m.arquivo = nome
            for t in m.topicos:
                t.arquivo = nome
        if not result.phase_table.empty:
            result.phase_table["arquivo"] = nome
        reports.append(result)
    if len(reports) == 1:
        return reports[0]
    frames: list[pd.DataFrame] = []
    totals: dict[str, int] = {}
    for ix, r in enumerate(reports):
        if not r.phase_table.empty:
            df = r.phase_table.copy()
            if "tabela_id" in df:
                # IDs únicos por arquivo evitam que a interface some tabelas diferentes.
                df["tabela_id"] = pd.to_numeric(df["tabela_id"], errors="coerce").fillna(0).astype(int) + ix*10000
            frames.append(df)
        for key, val in r.totais_declarados.items():
            token, sep, resto = key.partition("|")
            if sep:
                totals[f"{int(token) + ix*10000}|{resto}"] = val
    notes = list(dict.fromkeys(note for r in reports for note in r.notas_provas))
    issues: list[AuditIssue] = []
    for r in reports:
        for issue in r.issues:
            issues.append(AuditIssue(issue.severidade, issue.codigo, f"[{r.arquivo}] {issue.mensagem}", issue.pagina))
    bancas = [b for b in dict.fromkeys(r.banca for r in reports) if b != "Não identificada"]
    return EditalReport(
        phase_table=pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PHASE_COLUMNS),
        materias=[m for r in reports for m in r.materias],
        section_preview="\n\n".join(f"=== {r.arquivo} ===\n{r.section_preview}" for r in reports),
        section_text="\n\n".join(r.section_text for r in reports if r.section_text),
        issues=issues, paginas=sum(r.paginas for r in reports),
        arquivo=f"{len(reports)} documentos",
        banca=" + ".join(bancas) if bancas else "Não identificada",
        totais_declarados=totals,
        documentos=[name for name, _ in documents],
        documentos_pendentes=list(dict.fromkeys(n for r in reports for n in r.documentos_pendentes)),
        notas_provas=notes,
    )


def build_report(pdf_path: str | Path) -> EditalReport:
    raw = extract_raw(pdf_path)
    if not raw.full_text.strip():
        raise ValueError("PDF sem texto pesquisável. Converta uma cópia escaneada para PDF pesquisável antes do envio.")
    banca = detect_banca(raw.full_text)
    phase_table, totals = extract_phase_table(raw)
    notes: list[str] = []
    if phase_table.empty:
        phase_table, notes = _provas_narrativas(raw)
        if phase_table.empty:
            gen_table, gen_notes = _provas_narrativas_generico(raw)
            if not gen_table.empty:
                phase_table, notes = gen_table, notes + gen_notes
        if not phase_table.empty:
            # Totais explicitamente declarados no texto, não calculados por suposição.
            for (ident, prova), part in phase_table.groupby(["tabela_id", "prova"]):
                totals[f"{ident}|{prova}|"] = int(part["num_itens"].sum())
    elif "escopo" not in phase_table:
        phase_table["escopo"] = "disciplina"
    if "origem_tipo" not in phase_table:
        phase_table["origem_tipo"] = "tabela"
    deps = _documentos_complementares(raw.full_text)
    issues: list[AuditIssue] = []
    try:
        start, _, section_text = find_section(raw.full_text)
    except ValueError:
        # Ausência de conteúdo no PDF principal não invalida suas outras informações.
        start, section_text = 0, ""
        if deps:
            issues.append(AuditIssue("alta", "CONTEUDO_EM_ANEXOS",
                "O documento remete a anexos externos para o conteúdo programático; envie também os arquivos complementares."))
        else:
            issues.append(AuditIssue("alta", "SECAO_NAO_LOCALIZADA",
                "A seção de conteúdo programático não foi reconhecida. Não foram criadas disciplinas fictícias."))
    materias: list[Materia] = []
    if section_text:
        for cargo, grupo, block, offset in _split_contexts(section_text):
            materias.extend(_parse_block(cargo, grupo, block, offset, raw, start))
    result = EditalReport(
        phase_table=phase_table, materias=materias,
        section_preview=section_text[:4000], section_text=section_text,
        issues=issues,
        paginas=len(raw.pages_text), arquivo=Path(pdf_path).name,
        banca=banca,
        documentos=[Path(pdf_path).name], documentos_pendentes=deps,
        notas_provas=notes, totais_declarados=totals,
    )
    if any(m.origem == "tabela" for m in materias):
        result.issues.append(AuditIssue(
            "informativa", "CONTEUDO_EM_TABELA",
            "Parte do conteúdo programático foi extraída de tabelas (colunas Disciplina/Conteúdo). Confira as células no PDF original."))
    _validate(result, raw)
    return result
