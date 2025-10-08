# -*- coding: utf-8 -*-
"""
Created on Tue Oct  7 10:13:35 2025

@author: acer
"""




import streamlit as st
import pandas as pd
from datetime import date, timedelta, datetime
import hashlib
import plotly.express as px
import plotly.graph_objects as go
from io import BytesIO
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from oauth2client.service_account import ServiceAccountCredentials
from PIL import Image
import base64
import os

# ---------------------------
# Configuración de la página
# ---------------------------
st.set_page_config(
    page_title="Collaborative Management System",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------------------
# Constantes (AJUSTA SI ES NECESARIO)
# ---------------------------
SHEET_NAME = "kanban_backend"
CREDENTIALS_FILE = "credenciales.json"  # si usas archivo local en lugar de st.secrets

# ---------------------------
# Conexión a Google Sheets
# ---------------------------
@st.cache_resource
def get_gsheet_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        # Preferir st.secrets (recomendado para despliegue)
        if "gcp_service_account" in st.secrets:
            creds_dict = st.secrets["gcp_service_account"]
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            return client.open(SHEET_NAME)
        else:
            # Si tienes archivo de credenciales local
            if os.path.exists(CREDENTIALS_FILE):
                creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
                client = gspread.authorize(creds)
                return client.open(SHEET_NAME)
            else:
                raise Exception("No se encontraron credenciales: usar st.secrets['gcp_service_account'] o credenciales.json")
    except Exception as e:
        st.error(f"Error en conexión Google Sheets: {e}")
        raise

# ---------------------------
# Funciones utilitarias y backend
# ---------------------------
def ensure_worksheets_exist():
    """Verifica y crea las hojas necesarias si no existen"""
    try:
        sheet = get_gsheet_connection()
        required_sheets = ["tasks", "task_collaborators", "task_interactions", "users", "task_items", "plant_machines"]
        existing_sheets = [ws.title for ws in sheet.worksheets()]
        for sheet_name in required_sheets:
            if sheet_name not in existing_sheets:
                new_worksheet = sheet.add_worksheet(title=sheet_name, rows=200, cols=20)
                if sheet_name == "tasks":
                    new_worksheet.update('A1', [['id', 'task', 'description', 'date', 'priority',
                                                'shift', 'start_date', 'due_date', 'status',
                                                'completion_date', 'progress', 'created_by', 'document_links']])
                elif sheet_name == "task_collaborators":
                    new_worksheet.update('A1', [['task_id', 'username']])
                elif sheet_name == "task_interactions":
                    new_worksheet.update('A1', [['id', 'task_id', 'username', 'action_type',
                                                'timestamp', 'comment_text', 'image_base64', 'new_status', 'progress_value']])
                elif sheet_name == "users":
                    new_worksheet.update('A1', [['username', 'password_hash', 'role']])
                elif sheet_name == "task_items":
                    new_worksheet.update('A1', [['id', 'task_id', 'item_name', 'status', 'progress', 'completion_date']])
                elif sheet_name == "plant_machines":
                    new_worksheet.update('A1', [['machine_id', 'machine_name', 'area', 'coord_x', 'coord_y',
                                                'machine_type', 'status', 'last_maintenance', 'next_maintenance']])
                st.success(f"Hoja '{sheet_name}' creada automáticamente")
    except Exception as e:
        st.error(f"Error al verificar hojas: {str(e)}")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_data(username):
    try:
        sheet = get_gsheet_connection()
        ws_users = sheet.worksheet("users")
        df_users = get_as_dataframe(ws_users)
        df_users = df_users.dropna(how='all')
        if df_users.empty:
            return None
        df_users.columns = df_users.columns.str.strip().str.lower()
        mask = df_users['username'].str.strip().str.lower() == username.strip().lower()
        user_row = df_users[mask]
        if not user_row.empty:
            return user_row.iloc[0].to_dict()
        return None
    except Exception as e:
        st.error(f"Error al cargar datos de usuario: {e}")
        return None

def login_user(username, password):
    if not username or not password:
        st.error("Usuario y contraseña son requeridos")
        return False
    user_data = get_user_data(username)
    if not user_data:
        st.error("Usuario no encontrado")
        return False
    stored_hash = user_data.get('password_hash', '').strip()
    if not stored_hash:
        st.error("Credenciales inválidas")
        return False
    provided_hash = hash_password(password)
    # comparación simple de hashes
    if provided_hash == stored_hash:
        st.session_state.logged_in = True
        st.session_state.username = username
        st.session_state.current_role = user_data.get('role', 'Colaborador').strip()
        st.success(f"Bienvenido, {username}!")
        return True
    st.error("Contraseña incorrecta")
    return False

# ---------------------------
# Operaciones con tareas, items, interacciones
# ---------------------------
def load_tasks_from_db():
    """Carga tareas, colaboradores, interacciones y items; arma st.session_state.kanban y all_tasks_df"""
    try:
        sheet = get_gsheet_connection()
        ws_tasks = sheet.worksheet("tasks")
        ws_collab = sheet.worksheet("task_collaborators")
        ws_inter = sheet.worksheet("task_interactions")
        ws_items = sheet.worksheet("task_items")

        df_tasks_raw = get_as_dataframe(ws_tasks)
        df_collab_raw = get_as_dataframe(ws_collab)
        df_inter_raw = get_as_dataframe(ws_inter)
        df_items_raw = get_as_dataframe(ws_items)

        df_tasks = df_tasks_raw[df_tasks_raw.iloc[:, 0].notna()].copy() if not df_tasks_raw.empty else pd.DataFrame(columns=['id', 'task', 'description', 'date', 'priority', 'shift', 'start_date', 'due_date', 'status', 'completion_date', 'progress', 'created_by', 'document_links'])
        df_collab = df_collab_raw[df_collab_raw.iloc[:, 0].notna()].copy() if not df_collab_raw.empty else pd.DataFrame(columns=['task_id', 'username'])
        df_inter = df_inter_raw[df_inter_raw.iloc[:, 0].notna()].copy() if not df_inter_raw.empty else pd.DataFrame(columns=['id', 'task_id', 'username', 'action_type', 'timestamp', 'comment_text', 'image_base64', 'new_status', 'progress_value'])
        df_items = df_items_raw[df_items_raw.iloc[:, 0].notna()].copy() if not df_items_raw.empty else pd.DataFrame(columns=['id', 'task_id', 'item_name', 'status', 'progress', 'completion_date'])

        kanban_data = {"Por hacer": [], "En proceso": [], "Hecho": []}
        all_tasks_list = []

        if not df_tasks.empty:
            # Asegurar tipos
            df_tasks['id'] = pd.to_numeric(df_tasks['id'], errors='coerce').fillna(0).astype(int)
            if not df_collab.empty and 'task_id' in df_collab.columns:
                df_collab['task_id'] = pd.to_numeric(df_collab['task_id'], errors='coerce').fillna(-1).astype(int)
            if not df_inter.empty and 'task_id' in df_inter.columns:
                df_inter['task_id'] = pd.to_numeric(df_inter['task_id'], errors='coerce').fillna(-1).astype(int)
            if not df_items.empty and 'task_id' in df_items.columns:
                df_items['task_id'] = pd.to_numeric(df_items['task_id'], errors='coerce').fillna(-1).astype(int)

            for _, row in df_tasks.iterrows():
                task = row.to_dict()
                task_id = int(task['id'])
                responsables = []
                if not df_collab.empty:
                    responsables = df_collab[df_collab['task_id']==task_id]['username'].tolist()
                task['responsible_list'] = responsables
                task['responsible'] = ", ".join(responsables)
                interacciones = []
                if not df_inter.empty:
                    interacciones = df_inter[df_inter['task_id']==task_id].to_dict('records')
                task['interactions'] = interacciones
                # attach items summary
                items_for_task = []
                if not df_items.empty:
                    items_for_task = df_items[df_items['task_id']==task_id].to_dict('records')
                task['items'] = items_for_task

                status_val = task.get('status') or "Por hacer"
                if status_val in kanban_data:
                    kanban_data[status_val].append(task)
                else:
                    kanban_data["Por hacer"].append(task)
                all_tasks_list.append(task)

        st.session_state.kanban = kanban_data
        st.session_state.all_tasks_df = pd.DataFrame(all_tasks_list)

    except Exception as e:
        st.error(f"Error al cargar tareas: {e}")
        st.session_state.kanban = {"Por hacer": [], "En proceso": [], "Hecho": []}
        st.session_state.all_tasks_df = pd.DataFrame()

def add_task_to_db(task_data, initial_status, responsible_usernames):
    sheet = get_gsheet_connection()
    ws_tasks = sheet.worksheet("tasks")
    ws_collab = sheet.worksheet("task_collaborators")

    df_tasks = get_as_dataframe(ws_tasks)
    df_tasks = df_tasks[df_tasks.iloc[:, 0].notna()].copy() if not df_tasks.empty else pd.DataFrame(columns=['id','task','description','date','priority','shift','start_date','due_date','status','completion_date','progress','created_by','document_links'])
    new_id = 1
    if not df_tasks.empty and 'id' in df_tasks.columns:
        df_tasks['id'] = pd.to_numeric(df_tasks['id'], errors='coerce').fillna(0).astype(int)
        new_id = int(df_tasks["id"].max() + 1)
    task_data['id'] = new_id
    task_data['status'] = initial_status
    task_data['completion_date'] = None
    task_data['progress'] = 0
    task_data['created_by'] = st.session_state.username
    new_task_df = pd.DataFrame([task_data])

    # Alinear columnas
    existing_cols = df_tasks.columns.tolist()
    new_task_cols = new_task_df.columns.tolist()
    for col in existing_cols:
        if col not in new_task_cols:
            new_task_df[col] = None
    for col in new_task_cols:
        if col not in existing_cols:
            df_tasks[col] = None
    if not df_tasks.empty:
        new_task_df = new_task_df[df_tasks.columns]
    df_tasks = pd.concat([df_tasks, new_task_df], ignore_index=True)
    set_with_dataframe(ws_tasks, df_tasks)

    # colaboradores
    df_collab = get_as_dataframe(ws_collab)
    df_collab = df_collab[df_collab.iloc[:, 0].notna()].copy() if not df_collab.empty else pd.DataFrame(columns=['task_id','username'])
    new_collabs = pd.DataFrame([{"task_id": new_id, "username": u} for u in responsible_usernames])
    df_collab = pd.concat([df_collab, new_collabs], ignore_index=True)
    set_with_dataframe(ws_collab, df_collab)

    st.success("✅ Tarea agregada a Google Sheets.")
    load_tasks_from_db()

def update_task_status_in_db(task_id, new_status=None, completion_date=None, progress=None):
    sheet = get_gsheet_connection()
    ws = sheet.worksheet("tasks")
    df = get_as_dataframe(ws)
    df = df[df.iloc[:, 0].notna()].copy() if not df.empty else pd.DataFrame()
    if df.empty:
        return
    if 'id' in df.columns:
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(-1).astype(int)
    mask = df["id"] == task_id
    if new_status:
        df.loc[mask, "status"] = new_status
    if completion_date:
        df.loc[mask, "completion_date"] = completion_date
    if progress is not None:
        df.loc[mask, "progress"] = progress
    set_with_dataframe(ws, df)
    st.success("✅ Estado de tarea actualizado en Google Sheets.")
    load_tasks_from_db()

def add_task_interaction(task_id, username, action_type, comment_text=None, image_base64=None, new_status=None, progress_value=None):
    sheet = get_gsheet_connection()
    ws = sheet.worksheet("task_interactions")
    df = get_as_dataframe(ws)
    df = df[df.iloc[:, 0].notna()].copy() if not df.empty else pd.DataFrame(columns=['id','task_id','username','action_type','timestamp','comment_text','image_base64','new_status','progress_value'])
    new_id = 1
    if not df.empty and 'id' in df.columns:
        df['id'] = pd.to_numeric(df['id'], errors='coerce').fillna(0).astype(int)
        new_id = int(df['id'].max() + 1)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_row = {
        "id": new_id,
        "task_id": task_id,
        "username": username,
        "action_type": action_type,
        "timestamp": timestamp,
        "comment_text": comment_text,
        "image_base64": image_base64,
        "new_status": new_status,
        "progress_value": progress_value
    }
    new_row_df = pd.DataFrame([new_row])
    # mantener orden de columnas
    if not df.empty:
        for col in df.columns:
            if col not in new_row_df.columns:
                new_row_df[col] = None
        new_row_df = new_row_df[df.columns]
    df = pd.concat([df, new_row_df], ignore_index=True)
    set_with_dataframe(ws, df)
    st.success("Interacción registrada en Google Sheets.")
    load_tasks_from_db()

# -------------------------
# Funciones para items
# -------------------------
def add_items_to_task(task_id, items):
    sheet = get_gsheet_connection()
    ws_items = sheet.worksheet("task_items")
    df_items = get_as_dataframe(ws_items)
    df_items = df_items[df_items.iloc[:, 0].notna()].copy() if not df_items.empty else pd.DataFrame(columns=['id','task_id','item_name','status','progress','completion_date'])
    new_id = 1 if df_items.empty else int(pd.to_numeric(df_items["id"], errors='coerce').max() + 1)
    new_items = []
    for item in items:
        new_items.append({
            "id": new_id,
            "task_id": task_id,
            "item_name": item,
            "status": "Por hacer",
            "progress": 0,
            "completion_date": None
        })
        new_id += 1
    df_new_items = pd.DataFrame(new_items)
    df_items = pd.concat([df_items, df_new_items], ignore_index=True)
    set_with_dataframe(ws_items, df_items)
    st.success(f"✅ {len(new_items)} items agregados a la tarea {task_id}.")
    load_tasks_from_db()

def update_item_progress_in_db(item_id, new_status, progress, completion_date=None):
    sheet = get_gsheet_connection()
    ws_items = sheet.worksheet("task_items")
    df_items = get_as_dataframe(ws_items)
    df_items = df_items[df_items.iloc[:, 0].notna()].copy() if not df_items.empty else pd.DataFrame()
    if df_items.empty:
        return
    if 'id' in df_items.columns:
        df_items['id'] = pd.to_numeric(df_items['id'], errors='coerce').fillna(-1).astype(int)
    mask = df_items["id"] == item_id
    if new_status:
        df_items.loc[mask, "status"] = new_status
    if progress is not None:
        df_items.loc[mask, "progress"] = progress
    if completion_date:
        df_items.loc[mask, "completion_date"] = completion_date
    set_with_dataframe(ws_items, df_items)

def recalc_task_progress(task_id):
    ws_items = get_gsheet_connection().worksheet("task_items")
    df_items = get_as_dataframe(ws_items)
    df_items = df_items[df_items.iloc[:, 0].notna()].copy() if not df_items.empty else pd.DataFrame()
    if df_items.empty:
        return
    df_items['task_id'] = pd.to_numeric(df_items['task_id'], errors='coerce').fillna(-1).astype(int)
    task_items = df_items[df_items['task_id'] == task_id]
    if not task_items.empty:
        avg_progress = task_items['progress'].mean()
        update_task_status_in_db(task_id, None, progress=int(avg_progress))

# -------------------------
# Funciones para el plano permanente
# -------------------------
def cargar_maquinas_desde_db():
    """Carga las máquinas desde Google Sheets"""
    try:
        sheet = get_gsheet_connection()
        ws_machines = sheet.worksheet("plant_machines")
        df_machines = get_as_dataframe(ws_machines)
        df_machines = df_machines[df_machines.iloc[:, 0].notna()].copy()

        if not df_machines.empty:
            # Asegurar tipos de datos
            df_machines['coord_x'] = pd.to_numeric(df_machines['coord_x'], errors='coerce').fillna(0)
            df_machines['coord_y'] = pd.to_numeric(df_machines['coord_y'], errors='coerce').fillna(0)
            return df_machines
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"Error al cargar máquinas: {e}")
        return pd.DataFrame()

def guardar_maquina_en_db(maquina_data):
    """Guarda una nueva máquina en Google Sheets"""
    try:
        sheet = get_gsheet_connection()
        ws_machines = sheet.worksheet("plant_machines")
        df_machines = get_as_dataframe(ws_machines)
        df_machines = df_machines[df_machines.iloc[:, 0].notna()].copy() if not df_machines.empty else pd.DataFrame(columns=['machine_id', 'machine_name', 'area', 'coord_x', 'coord_y', 'machine_type', 'status', 'last_maintenance', 'next_maintenance'])

        # Generar nuevo ID
        new_id = 1
        if not df_machines.empty and 'machine_id' in df_machines.columns:
            df_machines['machine_id'] = pd.to_numeric(df_machines['machine_id'], errors='coerce').fillna(0).astype(int)
            new_id = int(df_machines["machine_id"].max() + 1)

        maquina_data['machine_id'] = new_id
        nueva_maquina_df = pd.DataFrame([maquina_data])

        # Alinear columnas
        if not df_machines.empty:
            for col in df_machines.columns:
                if col not in nueva_maquina_df.columns:
                    nueva_maquina_df[col] = None
            nueva_maquina_df = nueva_maquina_df[df_machines.columns]

        df_machines = pd.concat([df_machines, nueva_maquina_df], ignore_index=True)
        set_with_dataframe(ws_machines, df_machines)
        return True
    except Exception as e:
        st.error(f"Error al guardar máquina: {e}")
        return False

def mostrar_plano_permanente():
    """Muestra el plano permanente de la planta con máquinas reales"""
    st.header("🗺️ Plano de Planta - Distribución Real")
    st.markdown("---")

    # Cargar datos de máquinas desde Google Sheets
    df_machines = cargar_maquinas_desde_db()

    if df_machines.empty:
        st.warning("""
        **No hay máquinas configuradas en el plano.**

        Para usar esta funcionalidad:
        1. Ve a la pestaña "⚙️ Configurar Plano" (solo administradores)
        2. Agrega las máquinas de tu planta con sus coordenadas
        3. Las tareas se vincularán automáticamente por nombre de máquina
        """)
        return

    # Cargar tareas para vincular
    tareas_activas = st.session_state.all_tasks_df

    # Filtros
    col1, col2, col3 = st.columns(3)
    with col1:
        area_filtro = st.selectbox(
            "Filtrar por Área:",
            ["Todas"] + list(df_machines['area'].unique())
        )
    with col2:
        estado_filtro = st.selectbox(
            "Filtrar por Estado:",
            ["Todos"] + list(df_machines['status'].unique())
        )
    with col3:
        st.metric("Máquinas", len(df_machines))

    # Aplicar filtros
    if area_filtro != "Todas":
        df_machines = df_machines[df_machines['area'] == area_filtro]
    if estado_filtro != "Todos":
        df_machines = df_machines[df_machines['status'] == estado_filtro]

    # Crear plano interactivo
    fig = go.Figure()

    # 🎯 MODIFICA ESTAS ÁREAS SEGÚN TU PLANTA REAL
    areas_planta = {
        'Fabricación': {'x': [0, 600], 'y': [0, 400], 'color': 'lightblue'},
        'Soldadoras (Rotays)': {'x': [600, 1000], 'y': [0, 400], 'color': 'lightgreen'},
        'Ensamble Final': {'x': [0, 400], 'y': [400, 800], 'color': 'lightyellow'},
        'Almacén MP': {'x': [400, 800], 'y': [400, 800], 'color': 'lightcoral'},
        'Oficinas Técnicas': {'x': [800, 1000], 'y': [400, 600], 'color': 'lavender'},
        'Taller Mantenimiento': {'x': [800, 1000], 'y': [600, 800], 'color': 'wheat'},
        'Vestidores': {'x': [0, 200], 'y': [800, 1000], 'color': 'lightgray'},
        'Laboratorio': {'x': [200, 600], 'y': [800, 1000], 'color': 'lightpink'}
    }

    # Dibujar áreas CON HOVER MEJORADO
    for area_name, area_coords in areas_planta.items():
        # Calcular centro del área para posicionar el texto
        center_x = (area_coords['x'][0] + area_coords['x'][1]) / 2
        center_y = (area_coords['y'][0] + area_coords['y'][1]) / 2

        # Área con hover
        fig.add_trace(go.Scatter(
            x=[area_coords['x'][0], area_coords['x'][1], area_coords['x'][1], area_coords['x'][0], area_coords['x'][0]],
            y=[area_coords['y'][0], area_coords['y'][0], area_coords['y'][1], area_coords['y'][1], area_coords['y'][0]],
            fill="toself",
            fillcolor=area_coords['color'],
            opacity=0.2,
            line=dict(color="gray", width=1),
            name=area_name,
            showlegend=True,
            hoverinfo="text",
            hovertext=f"""
            <b>🏭 {area_name}</b><br>
            📏 Dimensiones: {area_coords['x'][1]-area_coords['x'][0]}x{area_coords['y'][1]-area_coords['y'][0]}<br>
            📍 Coordenadas: ({area_coords['x'][0]}-{area_coords['x'][1]}, {area_coords['y'][0]}-{area_coords['y'][1]})
            """,
            text=""
        ))

        # Texto del área centrado (sin hover)
        fig.add_trace(go.Scatter(
            x=[center_x],
            y=[center_y],
            mode='text',
            text=area_name,
            textfont=dict(
                color='black',
                size=12,
                family="Arial",
                weight="bold"
            ),
            showlegend=False,
            hoverinfo='skip'
        ))

    # Agregar máquinas CON HOVER DETALLADO
    for _, maquina in df_machines.iterrows():
        # Contar tareas asociadas a esta máquina
        tareas_maquina = tareas_activas[
            (tareas_activas['task'].str.contains(maquina['machine_name'], na=False, case=False)) |
            (tareas_activas['description'].str.contains(maquina['machine_name'], na=False, case=False))
        ]
        num_tareas = len(tareas_maquina)

        # Obtener información detallada de tareas
        tareas_info = ""
        if num_tareas > 0:
            tareas_info = "<br>📋 <b>Tareas Activas:</b>"
            for i, (_, tarea) in enumerate(tareas_maquina.head(3).iterrows()):  # Mostrar máximo 3 tareas
                estado_icono = {
                    'Por hacer': '🔴',
                    'En proceso': '🟡',
                    'Hecho': '🟢'
                }.get(tarea['status'], '⚪')

                tareas_info += f"<br>{estado_icono} {tarea['task']} ({tarea['progress']}%)"

            if num_tareas > 3:
                tareas_info += f"<br>... y {num_tareas - 3} más"

        # Definir color según estado y tareas pendientes
        if num_tareas > 0:
            color = '#FF4444'  # Rojo - con tareas pendientes
            estado_emoji = '🔴'
        elif maquina['status'] == 'Operativa':
            color = '#44FF44'  # Verde - operativa
            estado_emoji = '🟢'
        elif maquina['status'] == 'Mantenimiento':
            color = '#FFFF44'  # Amarillo - en mantenimiento
            estado_emoji = '🟡'
        else:
            color = '#888888'  # Gris - inactiva
            estado_emoji = '⚪'

        # HOVER DETALLADO para máquinas
        hover_info = f"""
        <b>🔧 {maquina['machine_name']}</b><br>
        {estado_emoji} <b>Estado:</b> {maquina['status']}<br>
        🏭 <b>Área:</b> {maquina['area']}<br>
        📍 <b>Posición:</b> ({maquina['coord_x']}, {maquina['coord_y']})<br>
        📊 <b>Tareas activas:</b> {num_tareas}
        {tareas_info}
        """

        if maquina.get('machine_type'):
            hover_info += f"<br>⚙️ <b>Tipo:</b> {maquina['machine_type']}"

        if maquina.get('last_maintenance') and str(maquina['last_maintenance']) != 'N/A':
            hover_info += f"<br>📅 <b>Último mantenimiento:</b> {maquina['last_maintenance']}"

        if maquina.get('next_maintenance') and str(maquina['next_maintenance']) != 'N/A':
            hover_info += f"<br>⏰ <b>Próximo mantenimiento:</b> {maquina['next_maintenance']}"

        fig.add_trace(go.Scatter(
            x=[maquina['coord_x']],
            y=[maquina['coord_y']],
            mode='markers+text',
            marker=dict(
                size=25,
                color=color,
                symbol='square',
                line=dict(width=2, color='black'),
                opacity=0.9
            ),
            text=maquina['machine_name'],
            textposition="top center",
            textfont=dict(
                color="black",
                size=10,
                family="Arial"
            ),
            name=maquina['machine_name'],
            hovertemplate=hover_info + "<extra></extra>",
            # Configuración del hover para mejor legibilidad
            hoverlabel=dict(
                bgcolor="white",
                bordercolor="gray",
                font=dict(
                    color="black",
                    size=12,
                    family="Arial"
                ),
                align="left"
            )
        ))

    # Configurar el plano
    fig.update_layout(
        title="Plano de Planta - Distribución Real",
        height=700,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 1000]),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 1000]),
        showlegend=True,
        plot_bgcolor='white',
        # Configuración global del hover
        hoverlabel=dict(
            bgcolor="white",
            bordercolor="gray",
            font_size=12,
            font_family="Arial",
            font_color="black",
            align="left"
        )
    )

    st.plotly_chart(fig, use_container_width=True)

    # Instrucciones para el usuario
    st.markdown("""
    **🔍 Información interactiva:**
    - **Pasa el mouse sobre las áreas** para ver detalles del departamento
    - **Pasa el mouse sobre las máquinas** para ver información detallada
    - **Haz clic en los elementos** de la leyenda para filtrar
    """)

    # Panel de información detallada (opcional, puedes mantenerlo o quitarlo)
    st.markdown("---")
    st.subheader("📋 Detalle Completo de Máquinas")

    for _, maquina in df_machines.iterrows():
        tareas_maquina = tareas_activas[
            (tareas_activas['task'].str.contains(maquina['machine_name'], na=False, case=False)) |
            (tareas_activas['description'].str.contains(maquina['machine_name'], na=False, case=False))
        ]

        with st.expander(f"🔧 {maquina['machine_name']} - {maquina['area']} ({len(tareas_maquina)} tareas)"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Tipo:** {maquina.get('machine_type', 'N/A')}")
                st.write(f"**Estado:** {maquina['status']}")
                st.write(f"**Último mantenimiento:** {maquina.get('last_maintenance', 'N/A')}")
                st.write(f"**Próximo mantenimiento:** {maquina.get('next_maintenance', 'N/A')}")
                st.write(f"**Coordenadas:** ({maquina['coord_x']}, {maquina['coord_y']})")

            with col2:
                if len(tareas_maquina) > 0:
                    st.warning(f"⚠️ {len(tareas_maquina)} tareas activas")
                    for _, tarea in tareas_maquina.iterrows():
                        estado_color = {
                            'Por hacer': '🔴',
                            'En proceso': '🟡',
                            'Hecho': '🟢'
                        }.get(tarea['status'], '⚪')

                        st.write(f"{estado_color} **{tarea['task']}** - {tarea['progress']}%")
                        if tarea.get('description'):
                            st.caption(f"_{tarea['description']}_")
                else:
                    st.success("✅ Sin tareas activas")

def configurar_plano_plantas():
    """Interfaz para configurar las máquinas en el plano"""
    st.header("⚙️ Configurar Plano de Planta")
    st.markdown("---")

    st.info("""
    **Instrucciones:**
    1. Define las áreas de tu planta en el código (coordenadas fijas)
    2. Agrega las máquinas con sus coordenadas exactas
    3. Las tareas se vincularán automáticamente por nombre de máquina
    """)

    # Mostrar estructura actual
    st.subheader("Estructura actual del plano")

    areas_planta = {
        'Producción': '0-400, 0-300',
        'Ensamblaje': '400-800, 0-300',
        'Control Calidad': '0-400, 300-600',
        'Almacén': '400-800, 300-600',
        'Oficinas': '800-1000, 0-200',
        'Mantenimiento': '800-1000, 200-600'
    }

    st.write("**Áreas definidas:**")
    for area, coords in areas_planta.items():
        st.write(f"- {area}: {coords}")

    # Gestión de máquinas existentes
    st.subheader("Máquinas Registradas")

    df_machines = cargar_maquinas_desde_db()

    if not df_machines.empty:
        st.write(f"**Total de máquinas:** {len(df_machines)}")

        # Mostrar máquinas por área
        for area in df_machines['area'].unique():
            maquinas_area = df_machines[df_machines['area'] == area]
            with st.expander(f"📁 {area} ({len(maquinas_area)} máquinas)"):
                for _, maquina in maquinas_area.iterrows():
                    st.write(f"• **{maquina['machine_name']}** - {maquina['status']} - ({maquina['coord_x']}, {maquina['coord_y']})")
    else:
        st.warning("No hay máquinas registradas. Usa el formulario below para agregar la primera máquina.")

    # Formulario para agregar máquinas
    st.markdown("---")
    st.subheader("Agregar Nueva Máquina")

    with st.form("agregar_maquina"):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre de la máquina*")
            area = st.selectbox("Área*", list(areas_planta.keys()))
            tipo = st.selectbox("Tipo", ["Producción", "Ensamblaje", "Control", "Almacenamiento", "Otro"])

        with col2:
            coord_x = st.number_input("Coordenada X*", 0, 1000, 100)
            coord_y = st.number_input("Coordenada Y*", 0, 600, 100)
            estado = st.selectbox("Estado*", ["Operativa", "Mantenimiento", "Inactiva"])
            proximo_mantenimiento = st.date_input("Próximo mantenimiento", value=date.today() + timedelta(days=30))

        if st.form_submit_button("✅ Agregar Máquina al Plano"):
            if nombre and area:
                maquina_data = {
                    'machine_name': nombre,
                    'area': area,
                    'coord_x': coord_x,
                    'coord_y': coord_y,
                    'machine_type': tipo,
                    'status': estado,
                    'last_maintenance': date.today().strftime("%Y-%m-%d"),
                    'next_maintenance': proximo_mantenimiento.strftime("%Y-%m-%d")
                }

                if guardar_maquina_en_db(maquina_data):
                    st.success(f"✅ Máquina '{nombre}' agregada exitosamente al plano")
                    st.rerun()
                else:
                    st.error("❌ Error al guardar la máquina")
            else:
                st.error("❌ Completa los campos obligatorios")

# -------------------------
# Procesamiento de imágenes
# -------------------------
def process_image(uploaded_file, max_size=(800,600)):
    """
    Procesa y redimensiona una imagen para que se guarde en base64.
    Devuelve string base64 o None en error.
    """
    try:
        image = Image.open(uploaded_file)
        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')
        image.thumbnail(max_size, Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return img_str
    except Exception as e:
        st.error(f"Error al procesar la imagen: {e}")
        return None

# -------------------------
# Export / limpieza / usuarios
# -------------------------
def generate_excel_export():
    sheet = get_gsheet_connection()
    output = BytesIO()
    try:
        df_tasks = get_as_dataframe(sheet.worksheet("tasks"))
        df_collab = get_as_dataframe(sheet.worksheet("task_collaborators"))
        df_inter = get_as_dataframe(sheet.worksheet("task_interactions"))
        df_items = get_as_dataframe(sheet.worksheet("task_items"))
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            if not df_tasks.empty and df_tasks.iloc[:,0].notna().any():
                df_tasks.to_excel(writer, sheet_name='Tareas', index=False)
            else:
                pd.DataFrame(columns=['id','task','description','date','priority','shift','start_date','due_date','status','completion_date','progress','created_by','document_links']).to_excel(writer, sheet_name='Tareas', index=False)
            if not df_collab.empty and df_collab.iloc[:,0].notna().any():
                df_collab.to_excel(writer, sheet_name='Colaboradores', index=False)
            else:
                pd.DataFrame(columns=['task_id','username']).to_excel(writer, sheet_name='Colaboradores', index=False)
            if not df_inter.empty and df_inter.iloc[:,0].notna().any():
                df_inter.to_excel(writer, sheet_name='Interacciones', index=False)
            else:
                pd.DataFrame(columns=['id','task_id','username','action_type','timestamp','comment_text','image_base64','new_status','progress_value']).to_excel(writer, sheet_name='Interacciones', index=False)
            if not df_items.empty and df_items.iloc[:,0].notna().any():
                df_items.to_excel(writer, sheet_name='Items', index=False)
            else:
                pd.DataFrame(columns=['id','task_id','item_name','status','progress','completion_date']).to_excel(writer, sheet_name='Items', index=False)
        output.seek(0)
        return output
    except Exception as e:
        st.error(f"Error al generar archivo: {e}")
        return None

def clear_task_data_from_db():
    try:
        sheet = get_gsheet_connection()
        for ws_name in ["task_collaborators", "task_interactions", "tasks", "task_items", "users", "plant_machines"]:
            ws = sheet.worksheet(ws_name)
            ws.clear()
            # volver a crear encabezados
            if ws_name == "tasks":
                ws.update('A1', [['id','task','description','date','priority','shift','start_date','due_date','status','completion_date','progress','created_by','document_links']])
            elif ws_name == "task_collaborators":
                ws.update('A1', [['task_id','username']])
            elif ws_name == "task_interactions":
                ws.update('A1', [['id','task_id','username','action_type','timestamp','comment_text','image_base64','new_status','progress_value']])
            elif ws_name == "task_items":
                ws.update('A1', [['id','task_id','item_name','status','progress','completion_date']])
            elif ws_name == "users":
                ws.update('A1', [['username','password_hash','role']])
            elif ws_name == "plant_machines":
                ws.update('A1', [['machine_id','machine_name','area','coord_x','coord_y','machine_type','status','last_maintenance','next_maintenance']])
        st.success("Google Sheet limpiado correctamente.")
    except Exception as e:
        st.error(f"Error al limpiar Google Sheet: {e}")
    load_tasks_from_db()

def create_new_user_in_db(username, password, role):
    sheet = get_gsheet_connection()
    ws_users = sheet.worksheet("users")
    df_users = get_as_dataframe(ws_users)
    df_users = df_users[df_users.iloc[:,0].notna()].copy() if not df_users.empty else pd.DataFrame(columns=['username','password_hash','role'])
    if not df_users.empty and username in df_users['username'].values:
        st.error(f"El usuario '{username}' ya existe.")
        return False
    hashed_password = hash_password(password)
    new_user = {"username": username, "password_hash": hashed_password, "role": role}
    new_user_df = pd.DataFrame([new_user])
    if not df_users.empty:
        for col in df_users.columns:
            if col not in new_user_df.columns:
                new_user_df[col] = None
        new_user_df = new_user_df[df_users.columns]
    df_users = pd.concat([df_users, new_user_df], ignore_index=True)
    set_with_dataframe(ws_users, df_users)
    st.success(f"Usuario '{username}' creado exitosamente con rol '{role}'.")
    return True

def update_user_password_in_db(username, new_password):
    sheet = get_gsheet_connection()
    ws_users = sheet.worksheet("users")
    df_users = get_as_dataframe(ws_users)
    df_users = df_users[df_users.iloc[:,0].notna()].copy() if not df_users.empty else pd.DataFrame(columns=['username','password_hash','role'])
    mask = df_users["username"] == username
    if mask.any():
        df_users.loc[mask, "password_hash"] = hash_password(new_password)
        set_with_dataframe(ws_users, df_users)
        st.success(f"Contraseña para '{username}' actualizada exitosamente.")
        return True
    else:
        st.error(f"Usuario '{username}' no encontrado.")
        return False

# -------------------------
# Formateo / display
# -------------------------
def formatear_tarea_display(t):
    """Formatea HTML para mostrar tarjeta de tarea (usada en la vista Kanban)"""
    card_color = "#393E46"
    try:
        if t.get('status') == 'Hecho':
            card_color = "#4CAF50"
        elif t.get('status') in ['Por hacer', 'En proceso']:
            if t.get('due_date'):
                try:
                    task_due_date = date.fromisoformat(str(t['due_date']))
                    today = date.today()
                    if task_due_date <= today:
                        card_color = "#F44336"
                    elif task_due_date <= today + timedelta(days=3):
                        card_color = "#FFC107"
                except Exception:
                    pass
    except Exception:
        pass

    description_html = f"<br><strong>📝 Descripción:</strong> {t.get('description','')}" if t.get('description') else ""
    start_date_html = f"<br><strong>➡️ Inicio:</strong> {t.get('start_date')}" if t.get('start_date') else ""
    due_date_html = f"<br><strong>🔚 Término:</strong> {t.get('due_date')}" if t.get('due_date') else ""
    responsible_display = ", ".join(t.get('responsible_list', [])) or "Sin asignar"
    progress_val = int(t.get('progress', 0) or 0)
    progress_html = f"""
    <div style="width: 100%; background-color: #ddd; border-radius: 5px; margin-top: 8px; overflow: hidden;">
        <div style="width: {progress_val}%; background-color: #007bff; color: white; text-align: center; border-radius: 5px; padding: 2px 0;">
            {progress_val}%
        </div>
    </div>
    """
    created_by_html = f"<br><strong>👤 Creado por:</strong> {t.get('created_by', 'N/A')}" if t.get('created_by') else "<br><strong>👤 Creado por:</strong> N/A"

        # AÑADIDO: Mostrar enlaces a documentos - VERSIÓN CORREGIDA
    document_links_html = ""
    try:
        document_links_value = t.get('document_links', '')

        # Verificar que el valor sea string válido y no esté vacío
        if document_links_value and isinstance(document_links_value, str) and document_links_value.strip():
            links = [link.strip() for link in document_links_value.split('\n') if link.strip()]

            # Filtrar solo links válidos (que empiecen con http)
            valid_links = []
            for link in links:
                if link.startswith(('http://', 'https://', 'www.')):
                    # Asegurar que los links de www tengan http
                    if link.startswith('www.'):
                        link = 'https://' + link
                    valid_links.append(link)

            if valid_links:
                document_links_html = "<br><strong>📎 Documentos:</strong><br>"
                for i, link in enumerate(valid_links):
                    document_links_html += f'<a href="{link}" target="_blank" style="color: #87CEEB; margin-left: 10px;">📄 Documento {i+1}</a><br>'

    except Exception:
        # Si hay error, no mostrar documentos
        document_links_html = ""

    card_html = f"""
    <div style="background-color:{card_color}; color:white; padding: 10px; border-radius: 8px; margin-bottom: 10px;">
        <strong>🔧 Tarea:</strong> {t.get('task','Sin nombre')}
        {description_html}
        <br><strong>👷 Responsables:</strong> {responsible_display}
        {created_by_html}
        <br><strong>📅 Creada:</strong> {t.get('date','')}
        {start_date_html}
        {due_date_html}
        <br><strong>🧭 Turno:</strong> {t.get('shift','')}
        <br><strong>🔥 Prioridad:</strong> {t.get('priority','')}
        {document_links_html}  <!-- AÑADIDO: Enlaces a documentos -->
        {progress_html}
    </div>
    """
    return {'card_html': card_html, 'interactions': t.get('interactions', []), 'items': t.get('items', [])}

# -------------------------
# Interfaz (login + app)
# -------------------------
def initialize_app():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'username' not in st.session_state:
        st.session_state.username = None
    if 'current_role' not in st.session_state:
        st.session_state.current_role = None
    if 'form_cleared' not in st.session_state:
        st.session_state.form_cleared = False
    # asegurar hojas
    ensure_worksheets_exist()
    if 'kanban' not in st.session_state:
        load_tasks_from_db()

def login_screen():
    st.set_page_config(page_title="Login - Collaborative Management System", layout="centered")
    st.markdown("""
    <div style='text-align: center;'>
        <h1 style='color: #1E90FF; font-size: 2.2em;'>🚀 Collaborative Management System</h1>
        <p style='color: #FF8C00;'>ENGINEERED FOR PERFORMANCE</p>
        <div style='background: linear-gradient(90deg, #FF8C00, #1E90FF);
                    height: 3px; width: 50%; margin: 10px auto;'></div>
    </div>
    """, unsafe_allow_html=True)
    with st.container():
        col1, col2, col3 = st.columns([1,3,1])
        with col2:
            with st.form("main_login_form"):
                st.subheader("Acceso al Sistema")
                username = st.text_input("Usuario")
                password = st.text_input("Contraseña", type="password")
                if st.form_submit_button("Ingresar"):
                    if login_user(username, password):
                        st.rerun()
            st.markdown("---")
            st.caption("**Engineered by Erik Armenta, M.Eng.** | _Operational Excellence through Technology_")

def main_app():
    st.set_page_config(page_title="Sistema Kanban", layout="wide")
    # sidebar
    with st.sidebar:
        if st.session_state.logged_in:
            st.write(f"👤 Usuario: **{st.session_state.username}**")
            st.write(f"🎚️ Rol: **{st.session_state.current_role}**")
            if st.button("🔄 Refrescar Tablero", use_container_width=True):
                load_tasks_from_db()
                st.success("Tablero actualizado")
            if st.button("Cerrar Sesión", use_container_width=True):
                st.session_state.logged_in = False
                st.session_state.username = None
                st.session_state.current_role = None
                st.rerun()

    admin_roles = ["admin principal", "supervisor", "coordinador"]
    is_admin = (st.session_state.current_role or "").lower() in admin_roles

    # tabs - MODIFICADO: Agregar pestaña de plano permanente
    tab_names = ["📋 Tablero Kanban", "🗺️ Plano de Planta"]
    if is_admin:
        tab_names.insert(0, "➕ Agregar Tarea")
        tab_names.append("⚙️ Configurar Plano")  # Nueva pestaña para admin
        tab_names.append("📊 Estadísticas")
        tab_names.append("⚙️ Gestión Usuarios")
    tabs = st.tabs(tab_names)

    # --- Agregar Tarea (admin) ---
    if is_admin and "➕ Agregar Tarea" in tab_names:
        with tabs[tab_names.index("➕ Agregar Tarea")]:
            st.header("➕ Agregar Nueva Tarea")
            st.markdown("---")
            sheet = get_gsheet_connection()
            df_users = get_as_dataframe(sheet.worksheet("users"))
            df_users = df_users[df_users.iloc[:,0].notna()].copy() if not df_users.empty else pd.DataFrame()
            collab_users = []
            if not df_users.empty and 'role' in df_users.columns:
                # permitir asignar coordinadores y colaboradores
                collab_users = df_users[df_users['role'].str.lower().isin(["colaborador","coordinador","supervisor"])]['username'].tolist()
            with st.form("agregar_tarea", clear_on_submit=True):
                tarea = st.text_input("Nombre de la Tarea*", value="")
                description = st.text_area("Descripción de la Tarea (Opcional)", value="")
                items_raw = st.text_area("Items de la tarea (uno por línea) - opcional", value="")

                # AÑADIDO: Campo para enlaces de documentos
                document_links = st.text_area(
                    "🔗 Enlaces a documentos (uno por línea) - opcional",
                    value="",
                    help="Pega los enlaces públicos de Google Drive a los documentos relacionados"
                )

                responsables = st.multiselect("Seleccionar Responsables*", options=collab_users)
                fecha = st.date_input("Fecha de Creación*", date.today())
                fecha_inicial = st.date_input("Fecha Inicial (Opcional)", value=None)
                fecha_termino = st.date_input("Fecha Término (Opcional)", value=None)
                prioridad = st.selectbox("Prioridad*", ["Alta","Media","Baja"])
                turno = st.selectbox("Turno*", ["1er Turno","2do Turno","3er Turno"])
                destino = st.selectbox("Columna Inicial*", ["Por hacer","En proceso"])
                submit = st.form_submit_button("Crear Tarea")
                if submit:
                    if not tarea:
                        st.error("El nombre de la tarea es obligatorio")
                    elif not responsables:
                        st.error("Debe asignar al menos un responsable")
                    else:
                        nueva_tarea = {
                            "task": tarea,
                            "description": description,
                            "date": fecha.strftime("%Y-%m-%d"),
                            "priority": prioridad,
                            "shift": turno,
                            "start_date": fecha_inicial.strftime("%Y-%m-%d") if fecha_inicial else None,
                            "due_date": fecha_termino.strftime("%Y-%m-%d") if fecha_termino else None,
                            "document_links": document_links if document_links and document_links.strip() else ""
                        }
                        add_task_to_db(nueva_tarea, destino, responsables)
                        # agregar items si los hay
                        if items_raw.strip():
                            items = [i.strip() for i in items_raw.splitlines() if i.strip()]
                            # obtener last id
                            last_id = int(st.session_state.all_tasks_df["id"].max())
                            add_items_to_task(last_id, items)
                        st.session_state.form_cleared = True
                        st.rerun()

    # --- Tablero Kanban ---
    with tabs[tab_names.index("📋 Tablero Kanban")]:
        st.header("📋 Tablero Kanban")
        st.markdown("---")
        # refrescar manual
        if st.button("🔄 Refrescar Tablero", key="refresh_kanban_top"):
            load_tasks_from_db()
            st.success("Tablero actualizado")
        # filtro por responsable
        all_responsibles = []
        for status_list in st.session_state.kanban.values():
            for task in status_list:
                if 'responsible_list' in task:
                    all_responsibles.extend(task['responsible_list'])
        responsables_unicos = sorted(list(set(all_responsibles)))
        default_idx = 0
        if (st.session_state.current_role or "").lower() == "colaborador" and st.session_state.username in responsables_unicos:
            default_idx = responsables_unicos.index(st.session_state.username) + 1
        filtro_responsable = st.selectbox("👤 Filtrar por responsable:", ["(Todos)"] + responsables_unicos, index=default_idx)
        # columnas kanban
        cols = st.columns(3)
        estados = ["Por hacer","En proceso","Hecho"]
        # cargar items global
        try:
            df_items_global = get_as_dataframe(get_gsheet_connection().worksheet("task_items"))
            df_items_global = df_items_global[df_items_global.iloc[:,0].notna()].copy() if not df_items_global.empty else pd.DataFrame()
        except Exception:
            df_items_global = pd.DataFrame()
        for col, estado in zip(cols, estados):
            with col:
                st.markdown(f"### {estado}")
                tareas_estado = st.session_state.kanban.get(estado, [])
                tareas_mostrar = [t for t in tareas_estado if filtro_responsable == "(Todos)" or filtro_responsable in t.get('responsible_list',[])]
                if not tareas_mostrar:
                    st.info("No hay tareas en esta sección.")
                    continue
                for task in tareas_mostrar:
                    task_display = formatear_tarea_display(task)
                    st.markdown(task_display['card_html'], unsafe_allow_html=True)
                    # Mostrar items dentro de la tarjeta (compacto)
                    items_task = []
                    if not df_items_global.empty and 'task_id' in df_items_global.columns:
                        df_items_global['task_id'] = pd.to_numeric(df_items_global['task_id'], errors='coerce').fillna(-1).astype(int)
                        items_task = df_items_global[df_items_global['task_id']==int(task['id'])].to_dict('records')
                    if items_task:
                        with st.expander("📌 Items", expanded=False):
                            for item in items_task:
                                st.write(f"**{item.get('item_name')}** - {int(item.get('progress',0))}% [{item.get('status')}]")
                                # permiso para actualizar item (responsable o admin)
                                current_username = st.session_state.get('username')
                                if is_admin or (current_username and current_username in task.get('responsible_list',[])):
                                    with st.form(key=f"form_item_{item['id']}", clear_on_submit=False):
                                        new_prog = st.slider("Avance", 0, 100, int(item.get('progress',0)), 5, key=f"slider_item_{item['id']}")
                                        comment = st.text_input("Comentario (opcional)", key=f"comment_item_{item['id']}")
                                        evidencia = st.file_uploader("Evidencia (imagen) - opcional", type=['png','jpg','jpeg'], key=f"evidence_item_{item['id']}")
                                        submit_item = st.form_submit_button("Actualizar Item")
                                        if submit_item:
                                            imagen_b64 = None
                                            if evidencia:
                                                imagen_b64 = process_image(evidencia)
                                                if not imagen_b64:
                                                    st.error("Error procesando la imagen.")
                                                    st.stop()
                                            new_status = "Hecho" if new_prog==100 else ("En proceso" if new_prog>0 else "Por hacer")
                                            update_item_progress_in_db(int(item['id']), new_status, int(new_prog),
                                                                        date.today().strftime("%Y-%m-%d") if new_prog==100 else None)
                                            add_task_interaction(int(task['id']), st.session_state.username, "item_update", comment_text=comment, image_base64=imagen_b64, progress_value=int(new_prog))
                                            recalc_task_progress(int(task['id']))
                                            st.rerun()
                    # historial de interacciones
                    if task_display['interactions']:
                        with st.expander(f"📝 Historial ({len(task_display['interactions'])})", expanded=False):
                            for interaccion in task_display['interactions']:
                                if interaccion.get('comment_text'):
                                    st.caption(f"💬 {interaccion.get('username','Usuario')} - {interaccion.get('timestamp','Fecha')}")
                                    st.info(interaccion['comment_text'])
                                if interaccion.get('image_base64'):
                                    st.caption("📸 Evidencia adjunta")
                                    try:
                                        img_data = base64.b64decode(interaccion['image_base64'])
                                        st.image(img_data, use_container_width=True, caption="Evidencia visual")
                                    except Exception as e:
                                        st.error(f"Error al cargar imagen: {e}")
                                st.markdown("---")
                    # acciones para responsables/admin
                    if estado in ['Por hacer','En proceso']:
                        current_username = st.session_state.get('username')
                        if is_admin or (current_username and current_username in task.get('responsible_list',[])):
                            with st.expander(f"✏️ Actualizar {task.get('task')}", expanded=False):
                                with st.form(key=f"update_task_form_{task['id']}"):
                                    progreso_actual = int(task.get('progress',0) or 0)
                                    nuevo_progreso = st.slider("Porcentaje de avance:", 0, 100, progreso_actual, 5, key=f"progress_{task['id']}_form")
                                    comentario = st.text_area("Comentario:", key=f"comment_{task['id']}_form")
                                    evidencia = st.file_uploader("Subir evidencia (imagen):", type=["png","jpg","jpeg"], key=f"upload_{task['id']}_form")
                                    col1_form, col2_form = st.columns(2)
                                    with col1_form:
                                        submit_avance = st.form_submit_button("Guardar avance")
                                    with col2_form:
                                        submit_completar = st.form_submit_button("Marcar como completada")
                                    if submit_avance or submit_completar:
                                        imagen_b64 = None
                                        if evidencia:
                                            imagen_b64 = process_image(evidencia)
                                            if not imagen_b64:
                                                st.error("Error al procesar la imagen.")
                                                st.stop()
                                        if submit_completar:
                                            nuevo_estado = "Hecho"
                                            nuevo_progreso = 100
                                            fecha_completado = date.today().strftime("%Y-%m-%d")
                                        else:
                                            nuevo_estado = task.get('status')
                                            fecha_completado = None
                                        update_task_status_in_db(int(task['id']), nuevo_estado, fecha_completado, progress=int(nuevo_progreso))
                                        add_task_interaction(int(task['id']), st.session_state.username, 'status_change' if submit_completar else 'progress_update', comment_text=comentario, image_base64=imagen_b64, new_status=nuevo_estado, progress_value=int(nuevo_progreso))
                                        st.rerun()

    # --- Pestaña: Plano de Planta ---
    with tabs[tab_names.index("🗺️ Plano de Planta")]:
        mostrar_plano_permanente()

    # --- Pestaña: Configurar Plano (Solo admin) ---
    if is_admin and "⚙️ Configurar Plano" in tab_names:
        with tabs[tab_names.index("⚙️ Configurar Plano")]:
            configurar_plano_plantas()

     # --- Pestaña: Estadísticas (Solo admin) ---
    if is_admin and "📊 Estadísticas" in tab_names:
        with tabs[tab_names.index("📊 Estadísticas")]:
            st.header("📊 Estadísticas del Kanban")
            st.markdown("---")

            if st.session_state.all_tasks_df.empty:
                st.info("No hay datos de tareas para mostrar estadísticas.")
            else:
                df = st.session_state.all_tasks_df.copy()

                df['due_date'] = pd.to_datetime(df['due_date'], errors='coerce')
                df['start_date'] = pd.to_datetime(df['start_date'], errors='coerce')
                df['date'] = pd.to_datetime(df['date'], errors='coerce')

                # Métricas clave
                st.subheader("Métricas Clave")

                total_tareas = len(df)
                por_hacer = len(df[df['status'] == 'Por hacer'])
                en_proceso = len(df[df['status'] == 'En proceso'])
                completadas = len(df[df['status'] == 'Hecho'])

                hoy = date.today()
                vencidas = len(df[(df['due_date'].notna()) & (df['due_date'].dt.date < hoy) & (df['status'] != 'Hecho')])
                por_vencer = len(df[(df['due_date'].notna()) &
                                     (df['due_date'].dt.date >= hoy) &
                                     (df['due_date'].dt.date <= hoy + timedelta(days=3)) &
                                     (df['status'] != 'Hecho')])

                col1, col2, col3, col4, col5, col6 = st.columns(6)

                with col1:
                    st.metric("📊 Tareas totales", total_tareas)

                with col2:
                    st.metric("🔄 Por Hacer", por_hacer)

                with col3:
                    st.metric("⚙️ En Progreso", en_proceso)

                with col4:
                    st.metric("✅ Completadas", completadas)

                with col5:
                    st.metric("⏰ Vencidas", vencidas)

                with col6:
                    st.metric("⚠️ Por Vencer", por_vencer)

                st.markdown("---")

                # Gráfico de estado de tareas
                st.subheader("Estado de Tareas (Vencimiento)")

                estado_data = {
                    'Categoría': ['Vencidas', 'Por Vencer', 'Completadas'],
                    'Cantidad': [vencidas, por_vencer, completadas]
                }
                df_estado = pd.DataFrame(estado_data)

                fig_barras = px.bar(
                    df_estado,
                    x='Categoría',
                    y='Cantidad',
                    color='Categoría',
                    color_discrete_map={
                        'Vencidas': '#F44336',
                        'Por Vencer': '#FFC107',
                        'Completadas': '#4CAF50'
                    },
                    text='Cantidad'
                )
                fig_barras.update_layout(showlegend=False)
                st.plotly_chart(fig_barras, use_container_width=True)

                st.markdown("---")

                # Distribución por estado
                st.subheader("Distribución de Tareas por Estado")

                estado_tareas_data = {
                    'Estado': ['Por hacer', 'En proceso', 'Hecho'],
                    'Cantidad': [por_hacer, en_proceso, completadas]
                }
                df_estado_tareas = pd.DataFrame(estado_tareas_data)

                fig_estado = px.bar(
                    df_estado_tareas,
                    x='Estado',
                    y='Cantidad',
                    color='Estado',
                    color_discrete_map={
                        'Por hacer': '#FF9800',
                        'En proceso': '#2196F3',
                        'Hecho': '#4CAF50'
                    },
                    text='Cantidad'
                )
                fig_estado.update_layout(showlegend=False)
                st.plotly_chart(fig_estado, use_container_width=True)

                st.markdown("---")

                # Avance por responsable
                st.subheader("Avance por Responsable")

                df_filtered_responsibles = df[df['responsible_list'].apply(lambda x: isinstance(x, list) and len(x) > 0)]

                if not df_filtered_responsibles.empty:
                    df_flat = df_filtered_responsibles.explode('responsible_list')
                    df_responsable = df_flat.groupby(['responsible_list', 'status']).size().unstack(fill_value=0)
                    df_responsable = df_responsable.reset_index().melt(id_vars='responsible_list',
                                                                        value_name='Cantidad',
                                                                        var_name='Estado')

                    fig_responsable = px.bar(
                        df_responsable,
                        x='responsible_list',
                        y='Cantidad',
                        color='Estado',
                        color_discrete_map={
                            'Por hacer': '#FF9800',
                            'En proceso': '#2196F3',
                            'Hecho': '#4CAF50'
                        },
                        barmode='group',
                        text='Cantidad'
                    )
                    fig_responsable.update_layout(xaxis_title='Responsable', yaxis_title='Cantidad de Tareas')
                    st.plotly_chart(fig_responsable, use_container_width=True)
                else:
                    st.warning("No hay datos de responsables asignados para mostrar el avance.")

                st.markdown("---")

                # Distribución por prioridad
                st.subheader("Distribución de Tareas por Prioridad")

                if 'priority' in df.columns:
                    prioridad_counts = df['priority'].value_counts().reset_index()
                    prioridad_counts.columns = ['Prioridad', 'Cantidad']

                    fig_prioridad = px.pie(
                        prioridad_counts,
                        values='Cantidad',
                        names='Prioridad',
                        hole=0.4,
                        color='Prioridad',
                        color_discrete_map={
                            'Alta': '#F44336',
                            'Media': '#FFC107',
                            'Baja': '#4CAF50'
                        }
                    )
                    fig_prioridad.update_traces(textposition='inside', textinfo='percent+label')
                    fig_prioridad.update_layout(showlegend=False)
                    st.plotly_chart(fig_prioridad, use_container_width=True)
                else:
                    st.warning("No hay datos de prioridad para mostrar.")

    # --- Pestaña: Gestión de Usuarios (Solo admin) ---
    if is_admin and "⚙️ Gestión Usuarios" in tab_names:
        with tabs[tab_names.index("⚙️ Gestión Usuarios")]:
            st.header("⚙️ Gestión de Usuarios")
            st.markdown("---")

            # Lista de usuarios existentes
            sheet = get_gsheet_connection()
            ws_users = sheet.worksheet("users")
            usuarios = get_as_dataframe(ws_users)
            usuarios = usuarios[usuarios.iloc[:, 0].notna()].copy() if not usuarios.empty else pd.DataFrame(columns=['username', 'password_hash', 'role'])

            if 'password_hash' in usuarios.columns:
                usuarios_display = usuarios[['username', 'role']].copy()
            else:
                usuarios_display = usuarios[['username', 'role']].copy()

            st.subheader("Usuarios Registrados")
            st.dataframe(usuarios_display)

            # Crear nuevo usuario
            st.markdown("---")
            st.subheader("Crear Nuevo Usuario")

            with st.form("nuevo_usuario"):
                nuevo_usuario = st.text_input("Nombre de usuario*")
                nueva_contraseña = st.text_input("Contraseña*", type="password")
                confirmar_contraseña = st.text_input("Confirmar contraseña*", type="password")
                rol = st.selectbox("Rol*", ["Admin Principal", "Supervisor", "Coordinador", "Colaborador"])

                if st.form_submit_button("Crear Usuario"):
                    if not nuevo_usuario or not nueva_contraseña or not confirmar_contraseña:
                        st.error("Todos los campos marcados con * son obligatorios")
                    elif nueva_contraseña != confirmar_contraseña:
                        st.error("Las contraseñas no coinciden")
                    else:
                        if create_new_user_in_db(nuevo_usuario, nueva_contraseña, rol):
                            st.rerun()

            # Cambiar contraseña
            st.markdown("---")
            st.subheader("Cambiar Contraseña")

            with st.form("cambiar_contraseña"):
                usuario_a_cambiar_pass = st.selectbox(
                    "Seleccionar usuario",
                    usuarios['username'].tolist() if not usuarios.empty else [],
                    key="select_user_pass_change"
                )
                nueva_contraseña_change = st.text_input("Nueva contraseña*", type="password", key="new_pass_change")
                confirmar_contraseña_change = st.text_input("Confirmar nueva contraseña*", type="password", key="confirm_pass_change")

                if st.form_submit_button("Actualizar Contraseña"):
                    if not nueva_contraseña_change or not confirmar_contraseña_change:
                        st.error("Todos los campos marcados con * son obligatorios")
                    elif nueva_contraseña_change != confirmar_contraseña_change:
                        st.error("Las contraseñas no coinciden")
                    else:
                        update_user_password_in_db(usuario_a_cambiar_pass, nueva_contraseña_change)
                        st.rerun()

            # Administración de la base de datos
            st.markdown("---")
            st.subheader("Administración de Base de Datos")

            with st.form("clear_data_form"):
                st.markdown("---")
                st.warning("Zona de peligro - Estas acciones no se pueden deshacer")
                confirmar = st.checkbox("Entiendo que esta acción borrará todos los datos de tareas y usuarios", key="confirm_clear_data")
                if st.form_submit_button("⚠️ Limpiar Base de Datos", type="primary"):
                    if confirmar:
                        clear_task_data_from_db()
                        st.rerun()
                    else:
                        st.error("Debe confirmar que entiende esta acción para continuar.")


# -------------------------
# Runner
# -------------------------
def run():
    initialize_app()
    if not st.session_state.logged_in:
        login_screen()
    else:
        main_app()

if __name__ == "__main__":
    run()


