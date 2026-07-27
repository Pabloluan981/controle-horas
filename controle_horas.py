# -*- coding: utf-8 -*-
"""
Controle de Horas - App Desktop
--------------------------------
App flutuante (always-on-top) para registrar tempo gasto em tarefas/tickets.
Usa SQLite para guardar os dados em um arquivo local (horas.db).
"""

import tkinter as tk
from tkinter import messagebox
import sqlite3
import os
import sys
import threading
import urllib.request
from datetime import datetime

# Versão atual do app — atualize esse número a cada novo commit
VERSION = "1.0.0"

# URLs do repositório público no GitHub
URL_VERSION = "https://raw.githubusercontent.com/Pabloluan981/controle-horas/master/version.txt"
URL_SCRIPT  = "https://raw.githubusercontent.com/Pabloluan981/controle-horas/master/controle_horas.py"

CAMINHO_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "horas.db")


# ============================================================
# CAMADA DE BANCO DE DADOS (SQLite)
# ============================================================

def conectar():
    return sqlite3.connect(CAMINHO_DB)


def criar_tabela():
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarefa TEXT NOT NULL,
            data TEXT NOT NULL,
            inicio TEXT NOT NULL,
            fim TEXT,
            duracao_horas REAL
        )
    """)
    # Tabela de configurações: guarda pares chave/valor.
    # Usamos INSERT OR IGNORE + UPDATE separado (padrão "upsert" no SQLite)
    # pra garantir que os valores padrão só são inseridos na primeira vez,
    # sem sobrescrever o que o usuário já salvou.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS configuracoes (
            chave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        )
    """)
    # Insere os valores padrão apenas se ainda não existirem
    cursor.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES ('almoco_inicio', '12:30')")
    cursor.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES ('almoco_fim', '14:00')")
    conn.commit()
    conn.close()


def ler_config(chave):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT valor FROM configuracoes WHERE chave = ?", (chave,))
    linha = cursor.fetchone()
    conn.close()
    return linha[0] if linha else None


def salvar_config(chave, valor):
    """
    INSERT OR REPLACE substitui a linha inteira se a chave já existe,
    ou insere uma nova linha se não existe — tudo numa só instrução.
    """
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO configuracoes (chave, valor) VALUES (?, ?)",
        (chave, valor)
    )
    conn.commit()
    conn.close()


def iniciar_tarefa_db(nome_tarefa):
    """
    INSERT INTO registros (..., fim, duracao_horas) VALUES (..., NULL, NULL)
    fim e duracao_horas começam como NULL — tarefa ainda não acabou.
    Os "?" são placeholders seguros (evitam SQL Injection).
    """
    agora = datetime.now()
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO registros (tarefa, data, inicio, fim, duracao_horas) VALUES (?, ?, ?, NULL, NULL)",
        (nome_tarefa, agora.strftime("%d/%m/%Y"), agora.isoformat())
    )
    conn.commit()
    novo_id = cursor.lastrowid
    conn.close()
    return novo_id, agora


def existe_tarefa_em_andamento():
    """
    SELECT id, tarefa, inicio FROM registros WHERE fim IS NULL LIMIT 1
    IS NULL porque NULL não pode ser comparado com "=" em SQL.
    """
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("SELECT id, tarefa, inicio FROM registros WHERE fim IS NULL LIMIT 1")
    linha = cursor.fetchone()
    conn.close()
    return linha


def parar_tarefa_db(id_registro, inicio_str):
    """
    UPDATE registros SET fim = ?, duracao_horas = ? WHERE id = ?
    UPDATE modifica linhas existentes. O WHERE id = ? garante que só
    a linha certa é alterada — sem WHERE, atualizaria a tabela inteira.
    """
    fim = datetime.now()
    inicio = datetime.fromisoformat(inicio_str)
    duracao = (fim - inicio).total_seconds() / 3600
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE registros SET fim = ?, duracao_horas = ? WHERE id = ?",
        (fim.isoformat(), duracao, id_registro)
    )
    conn.commit()
    conn.close()
    return duracao


def total_por_tarefa_do_dia(data_str):
    """
    SELECT tarefa, SUM(duracao_horas), MIN(inicio)
    FROM registros WHERE data = ? AND fim IS NOT NULL
    GROUP BY tarefa ORDER BY MIN(inicio) ASC

    GROUP BY agrupa linhas com o mesmo nome de tarefa, e SUM soma a
    duração dentro de cada grupo — assim sessões picadas do mesmo ticket
    viram um total só.
    """
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT tarefa, SUM(duracao_horas) AS total, MIN(inicio) AS primeiro_inicio
           FROM registros
           WHERE data = ? AND fim IS NOT NULL
           GROUP BY tarefa
           ORDER BY primeiro_inicio ASC""",
        (data_str,)
    )
    linhas = cursor.fetchall()
    conn.close()
    return [(tarefa, total) for tarefa, total, _ in linhas]


def dados_relatorio(periodo, referencia):
    """
    Busca o total de horas por tarefa para o período informado.

    periodo: 'dia', 'semana', 'mes' ou 'ano'
    referencia: objeto datetime que serve de âncora para o período

    Retorna lista de (tarefa, total_horas) ordenada do maior pro menor.

    A lógica de filtro varia por período:
    - dia: WHERE data = 'DD/MM/YYYY'
    - semana: WHERE data IN ('DD/MM/YYYY', ...) — os 7 dias da semana
    - mes: WHERE data LIKE 'DD/MM/YYYY' filtrando pelo mês/ano
    - ano: filtrando pelo ano

    Todos usam GROUP BY tarefa + SUM(duracao_horas) que você já conhece.
    """
    conn = conectar()
    cursor = conn.cursor()

    if periodo == "dia":
        data_str = referencia.strftime("%d/%m/%Y")
        cursor.execute("""
            SELECT tarefa, SUM(duracao_horas) as total
            FROM registros
            WHERE data = ? AND fim IS NOT NULL
            GROUP BY tarefa
            ORDER BY total DESC
        """, (data_str,))

    elif periodo == "semana":
        # Gera os 7 dias da semana que contém a data de referência
        # (segunda a domingo). strftime('%w') = dia da semana (0=domingo)
        dia_semana = referencia.weekday()  # 0=segunda, 6=domingo
        inicio = referencia - __import__('datetime').timedelta(days=dia_semana)
        datas = [(inicio + __import__('datetime').timedelta(days=i)).strftime("%d/%m/%Y") for i in range(7)]
        placeholders = ",".join("?" * len(datas))
        cursor.execute(f"""
            SELECT tarefa, SUM(duracao_horas) as total
            FROM registros
            WHERE data IN ({placeholders}) AND fim IS NOT NULL
            GROUP BY tarefa
            ORDER BY total DESC
        """, datas)

    elif periodo == "mes":
        # Filtra pelo padrão '__/MM/YYYY' usando LIKE
        filtro = referencia.strftime("%m/%Y")
        cursor.execute("""
            SELECT tarefa, SUM(duracao_horas) as total
            FROM registros
            WHERE data LIKE ? AND fim IS NOT NULL
            GROUP BY tarefa
            ORDER BY total DESC
        """, (f"%/{filtro}",))

    elif periodo == "ano":
        filtro = referencia.strftime("%Y")
        cursor.execute("""
            SELECT tarefa, SUM(duracao_horas) as total
            FROM registros
            WHERE data LIKE ? AND fim IS NOT NULL
            GROUP BY tarefa
            ORDER BY total DESC
        """, (f"%/{filtro}",))

    linhas = cursor.fetchall()
    conn.close()
    return linhas


def excluir_tarefa_do_dia(tarefa, data_str):
    """
    DELETE FROM registros WHERE tarefa = ? AND data = ?
    DELETE remove linhas. SEMPRE use WHERE — sem ele, apaga a tabela inteira.
    Os dois filtros juntos garantem que só o ticket daquele dia seja apagado.
    """
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM registros WHERE tarefa = ? AND data = ?",
        (tarefa, data_str)
    )
    conn.commit()
    conn.close()


def formatar_horas_hms(horas):
    total_segundos = int(round(horas * 3600))
    h, resto = divmod(total_segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ============================================================
# INTERFACE GRÁFICA (Tkinter)
# ============================================================

class AppControleHoras:

    TEMAS = {
        "claro": {
            "bg":               "#F5F6FA",
            "bg_card":          "#FFFFFF",
            "texto":            "#2D2D2D",
            "texto_secundario": "#6B7280",
            "cronometro":       "#1F2937",
            "entry_bg":         "#FFFFFF",
            "entry_fg":         "#2D2D2D",
            "borda":            "#E0E2E8",
            "iniciar_bg":       "#22C55E",
            "iniciar_hover":    "#16A34A",
            "parar_bg":         "#EF4444",
            "parar_hover":      "#DC2626",
            "ativo_bg":         "#BBF7D0",
            "ativo_borda":      "#16A34A",
            "hover_bg":         "#EFF1F7",
            "hover_borda":      "#9CA3AF",
            "icone_tema":       "☀️",
        },
        "escuro": {
            "bg":               "#1A1B23",
            "bg_card":          "#25262F",
            "texto":            "#E5E7EB",
            "texto_secundario": "#9CA3AF",
            "cronometro":       "#F9FAFB",
            "entry_bg":         "#2E2F3A",
            "entry_fg":         "#E5E7EB",
            "borda":            "#3A3B47",
            "iniciar_bg":       "#16A34A",
            "iniciar_hover":    "#15803D",
            "parar_bg":         "#DC2626",
            "parar_hover":      "#B91C1C",
            "ativo_bg":         "#1E4A30",
            "ativo_borda":      "#22C55E",
            "hover_bg":         "#33343F",
            "hover_borda":      "#6B7280",
            "icone_tema":       "🌙",
        },
    }

    def __init__(self, root):
        self.root = root
        self.root.title("Controle de Horas")
        self.root.geometry("320x560+100+100")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, True)
        self.root.maxsize(320, 560)

        self.id_tarefa_ativa = None
        self.nome_tarefa_ativa = None
        self.inicio_tarefa_ativa = None
        self.job_atualizacao = None
        self.tema_atual = "escuro"

        # Controle para evitar recriar a lista inteira a cada segundo (piscar)
        self.linhas_widgets = {}
        self.ordem_tarefas_exibida = []
        self.ultimo_tema_renderizado = None

        # Horários do almoço (lidos do banco, com fallback para o padrão)
        self.almoco_inicio = ler_config("almoco_inicio") or "12:30"
        self.almoco_fim    = ler_config("almoco_fim")    or "14:00"

        # Controle do popup de almoço: evita mostrar mais de uma vez por dia
        self.popup_almoco_mostrado = False
        self.popup_almoco_ref      = None  # referência à janela do popup

        self._montar_interface()
        self._aplicar_tema()
        self._verificar_tarefa_em_andamento()
        self._tick()
        self.root.after(100, self._aplicar_tema)
        # Verifica atualização em background (não trava a abertura do app)
        threading.Thread(target=self._verificar_atualizacao, daemon=True).start()  # redesenha botões após layout pronto

    # ----------------------------------------------------------
    # MONTAGEM DA INTERFACE
    # ----------------------------------------------------------

    def _montar_interface(self):
        # Topo: título + botão de tema
        self.frame_topo = tk.Frame(self.root)
        self.frame_topo.pack(fill="x", padx=15, pady=(12, 0))

        self.label_titulo = tk.Label(
            self.frame_topo, text="Controle de Horas", font=("Segoe UI", 12, "bold")
        )
        self.label_titulo.pack(side="left")

        self.botao_tema = tk.Button(
            self.frame_topo, text="🌙", font=("Segoe UI", 12),
            bd=0, relief="flat", cursor="hand2", command=self.alternar_tema
        )
        self.botao_tema.pack(side="right")

        self.botao_config = tk.Button(
            self.frame_topo, text="⚙️", font=("Segoe UI", 12),
            bd=0, relief="flat", cursor="hand2", command=self.abrir_configuracoes
        )
        self.botao_config.pack(side="right", padx=(0, 6))

        self.botao_relatorio = tk.Button(
            self.frame_topo, text="📊", font=("Segoe UI", 12),
            bd=0, relief="flat", cursor="hand2", command=self.abrir_relatorio
        )
        self.botao_relatorio.pack(side="right", padx=(0, 6))

        # Card do cronômetro
        self.frame_card = tk.Frame(self.root)
        self.frame_card.pack(fill="x", padx=15, pady=(12, 8))

        self.label_status = tk.Label(
            self.frame_card, text="⚪  Nenhuma tarefa ativa", font=("Segoe UI", 10)
        )
        self.label_status.pack(pady=(14, 2))

        self.label_cronometro = tk.Label(
            self.frame_card, text="00:00:00", font=("Consolas", 32, "bold")
        )
        self.label_cronometro.pack(pady=(0, 14))

        # Campo de texto
        self.entry_tarefa = tk.Entry(
            self.root, font=("Segoe UI", 11), justify="center", relief="flat", bd=8
        )
        self.entry_tarefa.pack(pady=(4, 10), padx=15, fill="x", ipady=4)
        self.entry_tarefa.insert(0, "Nome da tarefa...")
        self.entry_tarefa.bind("<FocusIn>", self._limpar_placeholder)

        # Botões Iniciar/Parar lado a lado (desenhados como Canvas pra ter cantos arredondados)
        self.frame_botoes = tk.Frame(self.root)
        self.frame_botoes.pack(padx=15, pady=(0, 8), fill="x")

        self.canvas_iniciar = tk.Canvas(
            self.frame_botoes, height=40, highlightthickness=0, cursor="hand2"
        )
        self.canvas_iniciar.pack(fill="x")
        self.canvas_iniciar.bind("<Button-1>", lambda e: self.iniciar())
        self.canvas_iniciar.bind("<Enter>",    lambda e: self._hover_botao(self.canvas_iniciar, "iniciar", True))
        self.canvas_iniciar.bind("<Leave>",    lambda e: self._hover_botao(self.canvas_iniciar, "iniciar", False))

        # Título e dica da lista
        self.label_lista_titulo = tk.Label(
            self.root, text="Tickets de hoje", font=("Segoe UI", 9, "bold"), anchor="w"
        )
        self.label_lista_titulo.pack(pady=(4, 0), padx=15, fill="x")

        self.label_lista_dica = tk.Label(
            self.root, text="clique num ticket para retomar ou pausar",
            font=("Segoe UI", 8), anchor="w"
        )
        self.label_lista_dica.pack(pady=(0, 4), padx=15, fill="x")

        # Área rolável com altura fixa
        self.frame_lista_container = tk.Frame(self.root, height=180)
        self.frame_lista_container.pack(padx=15, pady=(0, 0), fill="x")
        self.frame_lista_container.pack_propagate(False)

        self.canvas_lista = tk.Canvas(self.frame_lista_container, highlightthickness=0)

        self.frame_lista_interna = tk.Frame(self.canvas_lista)

        self.frame_lista_interna.bind(
            "<Configure>",
            lambda e: self._atualizar_scroll()
        )
        self.janela_lista_id = self.canvas_lista.create_window(
            (0, 0), window=self.frame_lista_interna, anchor="nw"
        )
        self.canvas_lista.bind(
            "<Configure>",
            lambda e: self.canvas_lista.itemconfig(self.janela_lista_id, width=e.width - 10)
        )
        self.canvas_lista.bind("<MouseWheel>", self._on_mousewheel)
        self.frame_lista_interna.bind("<MouseWheel>", self._on_mousewheel)

        # Scrollbar manual: um Canvas fino na lateral direita
        self.canvas_scroll = tk.Canvas(
            self.frame_lista_container, width=6, highlightthickness=0, cursor="hand2"
        )
        self.canvas_scroll.pack(side="right", fill="y", padx=(0, 2))
        self.canvas_lista.pack(side="left", fill="both", expand=True)

        # Total fixo fora da área rolável
        self.frame_total = tk.Frame(self.root)
        self.frame_total.pack(fill="x", padx=15, pady=(8, 12))

        self.label_total_dia = tk.Label(
            self.frame_total, text="Total hoje: 00:00:00",
            font=("Segoe UI", 11, "bold"), anchor="center"
        )
        self.label_total_dia.pack(fill="x")

    # ----------------------------------------------------------
    # BOTÕES ARREDONDADOS (Canvas)
    # ----------------------------------------------------------

    def _desenhar_botao(self, canvas, texto, cor):
        """
        Desenha um botão com cantos arredondados dentro de um Canvas.
        Tkinter não suporta border-radius nativo, então simulamos com
        4 arcos (cantos) + 2 retângulos (centro).
        """
        canvas.delete("all")
        canvas.update_idletasks()
        w = canvas.winfo_width() or 130
        h = canvas.winfo_height() or 40
        r = 10

        canvas.create_rectangle(r,   0,   w-r, h,   fill=cor, outline=cor)
        canvas.create_rectangle(0,   r,   w,   h-r, fill=cor, outline=cor)
        canvas.create_arc(0,     0,     r*2,   r*2,   start=90,  extent=90, fill=cor, outline=cor)
        canvas.create_arc(w-r*2, 0,     w,     r*2,   start=0,   extent=90, fill=cor, outline=cor)
        canvas.create_arc(0,     h-r*2, r*2,   h,     start=180, extent=90, fill=cor, outline=cor)
        canvas.create_arc(w-r*2, h-r*2, w,     h,     start=270, extent=90, fill=cor, outline=cor)

        canvas.create_text(
            w // 2, h // 2, text=texto,
            fill="white", font=("Segoe UI", 10, "bold")
        )

    def _hover_botao(self, canvas, tipo, entrando):
        cores = self.TEMAS[self.tema_atual]
        if tipo == "iniciar":
            cor    = cores["iniciar_hover"] if entrando else cores["iniciar_bg"]
            texto  = "▶   Iniciar"
        else:
            cor    = cores["parar_hover"] if entrando else cores["parar_bg"]
            texto  = "■   Parar"
        self._desenhar_botao(canvas, texto, cor)

    # ----------------------------------------------------------
    # TEMA
    # ----------------------------------------------------------

    def alternar_tema(self):
        self.tema_atual = "escuro" if self.tema_atual == "claro" else "claro"
        self.ultimo_tema_renderizado = None  # força reconstrução da lista com novas cores
        self._aplicar_tema()

    def _aplicar_tema(self):
        cores = self.TEMAS[self.tema_atual]

        self.root.config(bg=cores["bg"])
        self.frame_topo.config(bg=cores["bg"])
        self.frame_card.config(bg=cores["bg_card"])
        self.frame_botoes.config(bg=cores["bg"])
        self.frame_total.config(bg=cores["bg"])

        self.label_titulo.config(bg=cores["bg"], fg=cores["texto"])
        self.botao_tema.config(
            bg=cores["bg"], fg=cores["texto"],
            text=cores["icone_tema"], activebackground=cores["bg"]
        )
        self.botao_config.config(
            bg=cores["bg"], fg=cores["texto"], activebackground=cores["bg"]
        )
        self.botao_relatorio.config(
            bg=cores["bg"], fg=cores["texto"], activebackground=cores["bg"]
        )
        self.label_status.config(bg=cores["bg_card"], fg=cores["texto_secundario"])
        self.label_cronometro.config(bg=cores["bg_card"], fg=cores["cronometro"])

        self.entry_tarefa.config(
            bg=cores["entry_bg"], fg=cores["entry_fg"],
            insertbackground=cores["entry_fg"],
            highlightbackground=cores["borda"],
            highlightthickness=1, highlightcolor=cores["borda"]
        )

        self.canvas_iniciar.config(bg=cores["bg"])
        self._desenhar_botao(self.canvas_iniciar, "▶   Iniciar", cores["iniciar_bg"])

        self.label_lista_titulo.config(bg=cores["bg"], fg=cores["texto"])
        self.label_lista_dica.config(bg=cores["bg"], fg=cores["texto_secundario"])

        self.frame_lista_container.config(bg=cores["bg_card"])
        self.canvas_lista.config(bg=cores["bg_card"])
        self.canvas_scroll.config(bg=cores["bg_card"])
        self.frame_lista_interna.config(bg=cores["bg_card"])
        self._desenhar_thumb_scroll()

        self.label_total_dia.config(bg=cores["bg"], fg=cores["texto"])

        self._atualizar_lista_tarefas()

    # ----------------------------------------------------------
    # LOOP PRINCIPAL
    # ----------------------------------------------------------

    def _tick(self):
        if self.inicio_tarefa_ativa is not None:
            decorrido = datetime.now() - self.inicio_tarefa_ativa
            total_seg = int(decorrido.total_seconds())
            h, resto  = divmod(total_seg, 3600)
            m, s      = divmod(resto, 60)
            self.label_cronometro.config(text=f"{h:02d}:{m:02d}:{s:02d}")

        self._verificar_almoco()
        self._atualizar_lista_tarefas()
        self.job_atualizacao = self.root.after(1000, self._tick)

    # ----------------------------------------------------------
    # LISTA DE TICKETS
    # ----------------------------------------------------------

    def _atualizar_lista_tarefas(self):
        hoje  = datetime.now().strftime("%d/%m/%Y")
        dados = total_por_tarefa_do_dia(hoje)

        # Adiciona tempo ao vivo do ticket ativo (ainda não gravado no banco)
        if self.nome_tarefa_ativa is not None:
            elapsed = (datetime.now() - self.inicio_tarefa_ativa).total_seconds() / 3600
            nova_lista  = []
            encontrado  = False
            for tarefa, total in dados:
                if tarefa == self.nome_tarefa_ativa:
                    nova_lista.append((tarefa, total + elapsed))
                    encontrado = True
                else:
                    nova_lista.append((tarefa, total))
            if not encontrado:
                nova_lista.append((self.nome_tarefa_ativa, elapsed))
            dados = nova_lista

        cores        = self.TEMAS[self.tema_atual]
        nomes_atuais = [t for t, _ in dados]

        precisa_reconstruir = (
            nomes_atuais != self.ordem_tarefas_exibida
            or self.tema_atual != self.ultimo_tema_renderizado
            or not self.linhas_widgets
        )

        if not precisa_reconstruir:
            # Caminho rápido: só atualiza texto e destaque sem recriar nada
            for tarefa, total in dados:
                w     = self.linhas_widgets[tarefa]
                ativo = (tarefa == self.nome_tarefa_ativa)
                w["label_tempo"].config(text=formatar_horas_hms(total))
                if w["ativo"] != ativo:
                    bg     = cores["ativo_bg"] if ativo else cores["bg_card"]
                    peso   = "bold" if ativo else "normal"
                    prefixo = "🟢 " if ativo else "▶ "
                    w["linha"].config(bg=bg)
                    w["barra_destaque"].config(bg=cores["ativo_borda"] if ativo else bg)
                    w["label_nome"].config(text=f"{prefixo}{tarefa}", font=("Segoe UI", 9, peso), bg=bg)
                    w["label_tempo"].config(font=("Consolas", 9, peso), bg=bg)
                    w["label_lixeira"].config(bg=bg)
                    w["ativo"] = ativo
            total_geral = sum(t for _, t in dados)
            self.label_total_dia.config(text=f"Total hoje: {formatar_horas_hms(total_geral)}")
            return

        # Reconstrução completa
        for widget in self.frame_lista_interna.winfo_children():
            widget.destroy()
        self.linhas_widgets = {}

        if not dados:
            tk.Label(
                self.frame_lista_interna,
                text="Nenhum ticket registrado ainda hoje.",
                font=("Segoe UI", 9), bg=cores["bg_card"], fg=cores["texto_secundario"]
            ).pack(anchor="w", pady=8, padx=8)
        else:
            for tarefa, total in dados:
                ativo   = (tarefa == self.nome_tarefa_ativa)
                bg      = cores["ativo_bg"] if ativo else cores["bg_card"]
                peso    = "bold" if ativo else "normal"
                prefixo = "🟢 " if ativo else "▶ "

                linha = tk.Frame(
                    self.frame_lista_interna, bg=bg, cursor="hand2",
                    highlightthickness=1,
                    highlightbackground=cores["borda"],
                    highlightcolor=cores["borda"]
                )
                linha.pack(fill="x", pady=(0, 6), padx=4)

                barra_destaque = tk.Frame(
                    linha, bg=cores["ativo_borda"] if ativo else bg, width=5
                )
                barra_destaque.pack(side="left", fill="y")

                label_nome = tk.Label(
                    linha, text=f"{prefixo}{tarefa}",
                    font=("Segoe UI", 9, peso),
                    bg=bg, fg=cores["texto"], anchor="w", cursor="hand2"
                )
                label_nome.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=6)

                label_lixeira = tk.Label(
                    linha, text="🗑", font=("Segoe UI", 9),
                    bg=bg, fg=cores["texto_secundario"], cursor="hand2"
                )
                label_lixeira.pack(side="right", padx=(4, 8))

                label_tempo = tk.Label(
                    linha, text=formatar_horas_hms(total),
                    font=("Consolas", 9, peso),
                    bg=bg, fg=cores["texto"], cursor="hand2"
                )
                label_tempo.pack(side="right", padx=(0, 4))

                label_lixeira.bind("<Button-1>", lambda e, t=tarefa: self._excluir_ticket(t))

                for widget in (linha, label_nome, label_tempo):
                    widget.bind("<Button-1>", lambda e, t=tarefa: self._clicar_ticket(t))

                for widget in (linha, label_nome, label_tempo, label_lixeira):
                    widget.bind("<MouseWheel>", self._on_mousewheel)
                    widget.bind("<Enter>", lambda e, t=tarefa: self._hover_entrar(t))
                    widget.bind("<Leave>", lambda e, t=tarefa: self._hover_sair(t))

                self.linhas_widgets[tarefa] = {
                    "linha": linha, "barra_destaque": barra_destaque,
                    "label_nome": label_nome, "label_tempo": label_tempo,
                    "label_lixeira": label_lixeira, "ativo": ativo,
                }

        self.ordem_tarefas_exibida  = nomes_atuais
        self.ultimo_tema_renderizado = self.tema_atual

        self.frame_lista_interna.update_idletasks()
        self._atualizar_scroll()

        total_geral = sum(t for _, t in dados)
        self.label_total_dia.config(text=f"Total hoje: {formatar_horas_hms(total_geral)}")

    def abrir_relatorio(self):
        import plotly.graph_objects as go
        import tempfile, webbrowser, calendar, json
        from datetime import timedelta

        cores = self.TEMAS[self.tema_atual]
        bg, fg = cores["bg"], cores["texto"]

        # Busca dados de todos os períodos de uma vez
        hoje = datetime.now()

        def dados_json(periodo, ref):
            dados = dados_relatorio(periodo, ref)
            if not dados:
                return []
            total = sum(v for _, v in dados)
            return [{"tarefa": t, "horas": round(v * 3600),
                     "label": formatar_horas_hms(v),
                     "pct": round(v / total * 100, 1)} for t, v in dados]

        def gerar_periodos(tipo):
            resultados = []
            ref = hoje
            for _ in range(30):
                if tipo == "dia":
                    label = ref.strftime("%d/%m/%Y")
                    prox  = ref - timedelta(days=1)
                elif tipo == "semana":
                    ini   = ref - timedelta(days=ref.weekday())
                    label = f"{ini.strftime('%d/%m')} – {(ini + timedelta(days=6)).strftime('%d/%m/%Y')}"
                    prox  = ref - timedelta(weeks=1)
                elif tipo == "mes":
                    label = ref.strftime("%B/%Y")
                    mes   = ref.month - 1 or 12
                    ano   = ref.year - (1 if ref.month == 1 else 0)
                    dia   = min(ref.day, calendar.monthrange(ano, mes)[1])
                    prox  = ref.replace(year=ano, month=mes, day=dia)
                else:
                    label = ref.strftime("%Y")
                    prox  = ref.replace(year=ref.year - 1)
                resultados.append({"label": label, "dados": dados_json(tipo, ref)})
                ref = prox
            return resultados

        payload = {
            "dia":    gerar_periodos("dia"),
            "semana": gerar_periodos("semana"),
            "mes":    gerar_periodos("mes"),
            "ano":    gerar_periodos("ano"),
        }

        palette = [
            "#22C55E","#3B82F6","#F59E0B","#EF4444",
            "#A855F7","#06B6D4","#F97316","#EC4899",
            "#84CC16","#14B8A6","#6366F1","#FB923C"
        ]

        html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Controle de Horas — Relatório</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:{bg}; color:{fg}; font-family:'Segoe UI',sans-serif; padding:24px; }}
  h1 {{ text-align:center; font-size:22px; margin-bottom:20px; }}
  .filtros {{ display:flex; justify-content:center; gap:10px; margin-bottom:16px; }}
  .filtros button {{
    padding:8px 22px; border:none; border-radius:8px; cursor:pointer;
    font-size:14px; font-weight:bold; background:{cores["bg_card"]}; color:{fg};
    transition:background 0.2s;
  }}
  .filtros button.ativo {{ background:{cores["ativo_borda"]}; color:white; }}
  .nav {{ display:flex; justify-content:center; align-items:center; gap:16px; margin-bottom:8px; }}
  .nav button {{
    background:none; border:none; color:{fg}; font-size:20px; cursor:pointer; padding:4px 10px;
    border-radius:6px; transition:background 0.2s;
  }}
  .nav button:hover {{ background:{cores["bg_card"]}; }}
  .nav button:disabled {{ opacity:0.3; cursor:default; }}
  #periodo-label {{ font-size:15px; opacity:0.7; min-width:180px; text-align:center; }}
  #total-label {{ text-align:center; font-size:18px; font-weight:bold;
                  color:{cores["ativo_borda"]}; margin-bottom:16px; }}
  #grafico {{ width:100%; max-width:700px; margin:0 auto; }}
  #sem-dados {{ text-align:center; opacity:0.5; margin-top:60px; font-size:16px; }}
</style>
</head>
<body>
<h1>📊 Controle de Horas</h1>
<div class="filtros">
  <button onclick="trocar('dia')"    id="btn-dia">Dia</button>
  <button onclick="trocar('semana')" id="btn-semana">Semana</button>
  <button onclick="trocar('mes')"    id="btn-mes">Mês</button>
  <button onclick="trocar('ano')"    id="btn-ano">Ano</button>
</div>
<div class="nav">
  <button id="btn-ant" onclick="navegar(1)">◀</button>
  <span id="periodo-label"></span>
  <button id="btn-prox" onclick="navegar(-1)">▶</button>
</div>
<div id="total-label"></div>
<div id="grafico"></div>
<div id="sem-dados" style="display:none">Nenhum registro neste período.</div>

<script>
const dados   = {json.dumps(payload, ensure_ascii=False)};
const palette = {json.dumps(palette)};
let periodoAtual = 'dia';
let idxAtual     = 0;  // 0 = mais recente

function hms(segundos) {{
  const h = Math.floor(segundos / 3600).toString().padStart(2,'0');
  const m = Math.floor((segundos % 3600) / 60).toString().padStart(2,'0');
  const s = (segundos % 60).toString().padStart(2,'0');
  return h + ':' + m + ':' + s;
}}

function navegar(dir) {{
  const lista = dados[periodoAtual];
  const novoIdx = idxAtual + dir;
  if (novoIdx < 0 || novoIdx >= lista.length) return;
  idxAtual = novoIdx;
  renderizar();
}}

function trocar(periodo) {{
  periodoAtual = periodo;
  idxAtual = 0;
  ['dia','semana','mes','ano'].forEach(p =>
    document.getElementById('btn-' + p).classList.toggle('ativo', p === periodo)
  );
  renderizar();
}}

function renderizar() {{
  const lista = dados[periodoAtual];
  const d     = lista[idxAtual];

  document.getElementById('periodo-label').textContent = d.label;
  document.getElementById('btn-ant').disabled  = (idxAtual >= lista.length - 1);
  document.getElementById('btn-prox').disabled = (idxAtual <= 0);

  if (!d.dados.length) {{
    document.getElementById('total-label').textContent = '';
    document.getElementById('grafico').style.display = 'none';
    document.getElementById('sem-dados').style.display = 'block';
    return;
  }}

  document.getElementById('grafico').style.display = 'block';
  document.getElementById('sem-dados').style.display = 'none';

  const total = d.dados.reduce((s, x) => s + x.horas, 0);
  document.getElementById('total-label').textContent = 'Total: ' + hms(total);

  const trace = {{
    type: 'pie',
    labels: d.dados.map(x => x.tarefa),
    values: d.dados.map(x => x.horas),
    hole: 0.38,
    marker: {{ colors: d.dados.map((_, i) => palette[i % palette.length]),
               line: {{ color: '{bg}', width: 2 }} }},
    textinfo: 'percent',
    textfont: {{ size: 13, color: 'white' }},
    hovertemplate: '<b>%{{label}}</b><br>' +
                   '%{{customdata}}<br>' +
                   '%{{percent:.1%}}<extra></extra>',
    customdata: d.dados.map(x => x.label),
  }};

  Plotly.newPlot('grafico', [trace], {{
    paper_bgcolor: '{bg}',
    plot_bgcolor:  '{bg}',
    font: {{ color: '{fg}' }},
    legend: {{ font: {{ size: 12, color: '{fg}' }},
               bgcolor: 'rgba(0,0,0,0)' }},
    margin: {{ t: 20, b: 20, l: 20, r: 20 }},
  }}, {{responsive: true}});
}}

trocar('dia');
</script>
</body>
</html>"""

        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".html", prefix="controle_horas_"
        )
        tmp.write(html.encode("utf-8"))
        tmp.close()
        webbrowser.open(f"file:///{tmp.name}")

    # ----------------------------------------------------------
    # ATUALIZAÇÃO AUTOMÁTICA
    # ----------------------------------------------------------

    def _verificar_atualizacao(self):
        """
        Roda em background (thread separada) pra não travar o app.
        Busca o version.txt do GitHub e compara com a versão local.
        Se for diferente, avisa o usuário na thread principal via 'after'.
        """
        try:
            with urllib.request.urlopen(URL_VERSION, timeout=5) as resp:
                versao_remota = resp.read().decode("utf-8-sig").strip()

            if versao_remota != VERSION:
                # Usa after() para chamar o popup na thread principal do tkinter
                # (tkinter não é thread-safe — nunca atualize widgets de outras threads)
                self.root.after(0, lambda: self._oferecer_atualizacao(versao_remota))
        except Exception:
            pass  # sem internet ou GitHub fora — ignora silenciosamente

    def _oferecer_atualizacao(self, versao_remota):
        cores = self.TEMAS[self.tema_atual]
        ok = messagebox.askyesno(
            "Atualização disponível",
            f"Nova versão disponível: {versao_remota}\n"
            f"Versão atual: {VERSION}\n\n"
            f"Deseja atualizar agora?"
        )
        if not ok:
            return

        try:
            # Baixa o novo script direto por cima do arquivo atual
            caminho_atual = os.path.abspath(__file__)
            urllib.request.urlretrieve(URL_SCRIPT, caminho_atual)

            messagebox.showinfo(
                "Atualizado!",
                "Atualização concluída! Feche e abra o app novamente."
            )

        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível atualizar:\n{e}")

    # ----------------------------------------------------------
    # ALMOÇO
    # ----------------------------------------------------------

    def _verificar_almoco(self):
        """
        Chamada a cada segundo pelo _tick. Verifica se chegou no horário
        de início do almoço para mostrar o popup, e se chegou no horário
        de fim para fechar o popup e pausar retroativamente (caso o usuário
        tenha esquecido de responder).
        """
        if self.id_tarefa_ativa is None:
            return  # nenhuma tarefa rodando, nada a fazer

        agora_str = datetime.now().strftime("%H:%M")

        # Horário de início: mostra o popup (só uma vez por dia)
        if agora_str == self.almoco_inicio and not self.popup_almoco_mostrado:
            self.popup_almoco_mostrado = True
            self._mostrar_popup_almoco()

        # Horário de fim: se o popup ainda estiver aberto, fecha e pausa
        # retroativamente no horário de início do almoço
        if agora_str == self.almoco_fim and self.popup_almoco_ref is not None:
            self._pausar_retroativo(self.almoco_inicio)
            try:
                self.popup_almoco_ref.destroy()
            except Exception:
                pass
            self.popup_almoco_ref = None

    def _mostrar_popup_almoco(self):
        """
        Abre uma janela flutuante (também always-on-top) perguntando se
        o usuário quer pausar para o almoço. Fica aberta até o usuário
        responder ou até o horário de fim do almoço.
        """
        popup = tk.Toplevel(self.root)
        popup.title("Hora do almoço!")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.grab_set()

        # Centraliza sobre a janela principal
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 150
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 100
        popup.geometry(f"300x200+{x}+{y}")  # foca no popup enquanto estiver aberto

        cores = self.TEMAS[self.tema_atual]
        popup.config(bg=cores["bg"])

        tk.Label(
            popup, text="🍽️  Hora do almoço!", font=("Segoe UI", 12, "bold"),
            bg=cores["bg"], fg=cores["texto"]
        ).pack(pady=(20, 4), padx=24)

        tk.Label(
            popup,
            text=f"Quer pausar \"{self.nome_tarefa_ativa}\"?\n"
                 f"Se não responder até {self.almoco_fim}, pausa automaticamente\n"
                 f"em {self.almoco_inicio}.",
            font=("Segoe UI", 9), bg=cores["bg"], fg=cores["texto_secundario"],
            justify="center"
        ).pack(pady=(0, 16), padx=24)

        frame_btns = tk.Frame(popup, bg=cores["bg"])
        frame_btns.pack(pady=(0, 20), padx=24, fill="x")

        def sim():
            self.parar()
            popup.destroy()
            self.popup_almoco_ref = None

        def nao():
            popup.destroy()
            self.popup_almoco_ref = None

        tk.Button(
            frame_btns, text="Sim, pausar", font=("Segoe UI", 10, "bold"),
            bg=cores["iniciar_bg"], fg="white", relief="flat", cursor="hand2",
            command=sim
        ).pack(side="left", expand=True, fill="x", ipady=8, padx=(0, 4))

        tk.Button(
            frame_btns, text="Não, continuar", font=("Segoe UI", 10),
            bg=cores["bg_card"], fg=cores["texto"], relief="flat", cursor="hand2",
            command=nao
        ).pack(side="left", expand=True, fill="x", ipady=8, padx=(4, 0))

        self.popup_almoco_ref = popup

    def _pausar_retroativo(self, hora_str):
        """
        Para a tarefa ativa, mas usa como horário de fim o 'hora_str'
        passado (ex: '12:30'), em vez do horário atual.
        Isso permite que o banco grave o tempo correto mesmo que o usuário
        só tenha visto o popup horas depois.
        """
        if self.id_tarefa_ativa is None:
            return

        hoje = datetime.now().strftime("%Y-%m-%d")
        fim  = datetime.fromisoformat(f"{hoje}T{hora_str}:00")
        inicio = self.inicio_tarefa_ativa
        duracao = max((fim - inicio).total_seconds() / 3600, 0)

        conn = conectar()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE registros SET fim = ?, duracao_horas = ? WHERE id = ?",
            (fim.isoformat(), duracao, self.id_tarefa_ativa)
        )
        conn.commit()
        conn.close()

        self.id_tarefa_ativa     = None
        self.nome_tarefa_ativa   = None
        self.inicio_tarefa_ativa = None
        self.label_status.config(text="⚪  Nenhuma tarefa ativa")
        self.label_cronometro.config(text="00:00:00")
        self.ordem_tarefas_exibida = []

    # ----------------------------------------------------------
    # CONFIGURAÇÕES
    # ----------------------------------------------------------

    def abrir_configuracoes(self):
        cfg = tk.Toplevel(self.root)
        cfg.title("Configurações")
        cfg.attributes("-topmost", True)
        cfg.resizable(False, False)
        cfg.grab_set()

        # Centraliza sobre a janela principal
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 130
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 120
        cfg.geometry(f"+{x}+{y}")

        cores = self.TEMAS[self.tema_atual]
        cfg.config(bg=cores["bg"])

        tk.Label(
            cfg, text="⚙️  Horário do almoço", font=("Segoe UI", 11, "bold"),
            bg=cores["bg"], fg=cores["texto"]
        ).pack(pady=(20, 12), padx=24)

        frame_campos = tk.Frame(cfg, bg=cores["bg"])
        frame_campos.pack(padx=24, fill="x")

        def campo(label, valor_atual):
            tk.Label(
                frame_campos, text=label, font=("Segoe UI", 9),
                bg=cores["bg"], fg=cores["texto_secundario"], anchor="w"
            ).pack(fill="x")
            entry = tk.Entry(
                frame_campos, font=("Segoe UI", 11), justify="center",
                bg=cores["entry_bg"], fg=cores["entry_fg"],
                insertbackground=cores["entry_fg"], relief="flat",
                highlightthickness=1, highlightbackground=cores["borda"]
            )
            entry.insert(0, valor_atual)
            entry.pack(fill="x", ipady=6, pady=(2, 10))
            return entry

        entry_inicio = campo("Início do almoço (HH:MM)", self.almoco_inicio)
        entry_fim    = campo("Fim do almoço (HH:MM)",    self.almoco_fim)

        msg = tk.Label(cfg, text="", font=("Segoe UI", 8), bg=cores["bg"], fg="#EF4444")
        msg.pack()

        def salvar():
            ini = entry_inicio.get().strip()
            fim = entry_fim.get().strip()

            # Valida formato HH:MM
            try:
                datetime.strptime(ini, "%H:%M")
                datetime.strptime(fim, "%H:%M")
            except ValueError:
                msg.config(text="Formato inválido. Use HH:MM (ex: 12:30)")
                return

            salvar_config("almoco_inicio", ini)
            salvar_config("almoco_fim", fim)
            self.almoco_inicio = ini
            self.almoco_fim    = fim
            self.popup_almoco_mostrado = False  # reseta pra novo horário valer hoje
            cfg.destroy()

        tk.Button(
            cfg, text="Salvar", font=("Segoe UI", 10, "bold"),
            bg=cores["iniciar_bg"], fg="white", relief="flat", cursor="hand2",
            command=salvar
        ).pack(pady=(4, 20), padx=24, fill="x", ipady=8)

    # ----------------------------------------------------------
    # HOVER NOS TICKETS
    # ----------------------------------------------------------

    def _hover_entrar(self, tarefa):
        w = self.linhas_widgets.get(tarefa)
        if not w:
            return
        cores = self.TEMAS[self.tema_atual]
        bg    = cores["hover_bg"]
        w["linha"].config(bg=bg, highlightthickness=2,
                          highlightbackground=cores["hover_borda"],
                          highlightcolor=cores["hover_borda"])
        w["label_nome"].config(bg=bg)
        w["label_tempo"].config(bg=bg)
        w["label_lixeira"].config(bg=bg)
        if not w["ativo"]:
            w["barra_destaque"].config(bg=bg)

    def _hover_sair(self, tarefa):
        w = self.linhas_widgets.get(tarefa)
        if not w:
            return
        cores = self.TEMAS[self.tema_atual]
        ativo = w["ativo"]
        bg    = cores["ativo_bg"] if ativo else cores["bg_card"]
        w["linha"].config(bg=bg, highlightthickness=1,
                          highlightbackground=cores["borda"],
                          highlightcolor=cores["borda"])
        w["label_nome"].config(bg=bg)
        w["label_tempo"].config(bg=bg)
        w["label_lixeira"].config(bg=bg)
        w["barra_destaque"].config(bg=cores["ativo_borda"] if ativo else bg)

    # ----------------------------------------------------------
    # SCROLL
    # ----------------------------------------------------------

    def _atualizar_scroll(self):
        """Recalcula scrollregion e redesenha o thumb da scrollbar manual."""
        self.canvas_lista.configure(scrollregion=self.canvas_lista.bbox("all"))
        self._desenhar_thumb_scroll()

    def _desenhar_thumb_scroll(self):
        """
        Desenha o 'thumb' (a parte arrastável) da scrollbar manual.
        Calcula a posição e altura proporcionais ao conteúdo visível.
        """
        self.canvas_scroll.delete("all")
        cores = self.TEMAS[self.tema_atual]
        h_canvas  = self.frame_lista_container.winfo_height()
        bbox      = self.canvas_lista.bbox("all")
        if not bbox:
            return
        h_total   = bbox[3]
        if h_total <= h_canvas:
            return  # conteúdo cabe todo — não precisa mostrar thumb

        # Fração visível e posição atual do scroll (valores entre 0.0 e 1.0)
        frac_vis  = h_canvas / h_total
        top, _    = self.canvas_lista.yview()

        thumb_h   = max(int(frac_vis * h_canvas), 20)
        thumb_y   = int(top * h_canvas)

        r = 3  # raio dos cantos arredondados do thumb
        x0, x1 = 0, 6
        y0, y1 = thumb_y, thumb_y + thumb_h
        cor = cores["ativo_borda"]

        self.canvas_scroll.create_arc(x0, y0, x1, y0+r*2, start=90, extent=180, fill=cor, outline=cor)
        self.canvas_scroll.create_arc(x0, y1-r*2, x1, y1,  start=270, extent=180, fill=cor, outline=cor)
        self.canvas_scroll.create_rectangle(x0, y0+r, x1, y1-r, fill=cor, outline=cor)

    def _on_mousewheel(self, event):
        self.canvas_lista.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._desenhar_thumb_scroll()

    # ----------------------------------------------------------
    # AÇÕES DOS TICKETS
    # ----------------------------------------------------------

    def _clicar_ticket(self, tarefa):
        if tarefa == self.nome_tarefa_ativa:
            self.parar()
            return
        if self.id_tarefa_ativa is not None:
            parar_tarefa_db(self.id_tarefa_ativa, self.inicio_tarefa_ativa.isoformat())
        novo_id, inicio = iniciar_tarefa_db(tarefa)
        self.id_tarefa_ativa      = novo_id
        self.nome_tarefa_ativa    = tarefa
        self.inicio_tarefa_ativa  = inicio
        self.label_status.config(text=f"🟢 {tarefa}")
        self._atualizar_lista_tarefas()

    def _excluir_ticket(self, tarefa):
        if tarefa == self.nome_tarefa_ativa:
            messagebox.showwarning("Atenção", f"'{tarefa}' está em andamento. Pause antes de excluir.")
            return
        hoje   = datetime.now().strftime("%d/%m/%Y")
        dados  = dict(total_por_tarefa_do_dia(hoje))
        tempo  = formatar_horas_hms(dados.get(tarefa, 0))
        ok = messagebox.askyesno(
            "Confirmar exclusão",
            f"Apagar o ticket \"{tarefa}\" ({tempo}) registrado HOJE?\n\n"
            f"Isso NÃO afeta outros tickets nem registros de outros dias.\n"
            f"Essa ação não pode ser desfeita."
        )
        if not ok:
            return
        excluir_tarefa_do_dia(tarefa, hoje)
        self.ordem_tarefas_exibida = []  # força reconstrução
        self._atualizar_lista_tarefas()

    # ----------------------------------------------------------
    # INICIAR / PARAR
    # ----------------------------------------------------------

    def _verificar_tarefa_em_andamento(self):
        linha = existe_tarefa_em_andamento()
        if linha:
            self.id_tarefa_ativa, tarefa, inicio_str = linha
            self.nome_tarefa_ativa   = tarefa
            self.inicio_tarefa_ativa = datetime.fromisoformat(inicio_str)
            self.label_status.config(text=f"🟢 {tarefa}")

    def iniciar(self):
        nome = self.entry_tarefa.get().strip()
        if not nome or nome == "Nome da tarefa...":
            messagebox.showwarning("Atenção", "Digite o nome da tarefa antes de iniciar.")
            return
        if self.id_tarefa_ativa is not None:
            messagebox.showwarning("Atenção", "Já existe uma tarefa em andamento. Pare ela antes de iniciar outra.")
            return
        novo_id, inicio = iniciar_tarefa_db(nome)
        self.id_tarefa_ativa      = novo_id
        self.nome_tarefa_ativa    = nome
        self.inicio_tarefa_ativa  = inicio
        self.label_status.config(text=f"🟢 {nome}")
        self.entry_tarefa.delete(0, tk.END)

    def parar(self):
        if self.id_tarefa_ativa is None:
            messagebox.showinfo("Info", "Nenhuma tarefa em andamento no momento.")
            return
        parar_tarefa_db(self.id_tarefa_ativa, self.inicio_tarefa_ativa.isoformat())
        self.id_tarefa_ativa      = None
        self.nome_tarefa_ativa    = None
        self.inicio_tarefa_ativa  = None
        self.label_status.config(text="⚪  Nenhuma tarefa ativa")
        self.label_cronometro.config(text="00:00:00")
        self._atualizar_lista_tarefas()

    # ----------------------------------------------------------
    # UTILS
    # ----------------------------------------------------------

    def _limpar_placeholder(self, event):
        if self.entry_tarefa.get() == "Nome da tarefa...":
            self.entry_tarefa.delete(0, tk.END)


# ============================================================
# PONTO DE ENTRADA
# ============================================================

if __name__ == "__main__":
    criar_tabela()
    root = tk.Tk()
    app  = AppControleHoras(root)
    root.mainloop()