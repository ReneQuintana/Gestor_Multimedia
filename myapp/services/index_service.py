# services/index_service.py

def index_folder(folder: str, progress_callback=None):
    """
    Solo indexa archivos NUEVOS o MODIFICADOS.
    Si el path ya está en DB y mtime no cambió, lo salta.
    """
    repo = MediaRepository()
    
    for filepath in scan_media_files(folder):
        mtime = os.path.getmtime(filepath)
        
        # ¿Ya está indexado y no cambió?
        existing = repo.get_by_path(filepath)
        if existing and existing['mtime'] == mtime:
            continue  # saltar — no rehashear
        
        # Calcular hash solo si es necesario
        ext = os.path.splitext(filepath)[1].lower()
        h = get_image_hash(filepath) if ext in IMAGE_EXTS else get_file_hash(filepath)
        
        repo.upsert(filepath, mtime, h)
        
        if progress_callback:
            progress_callback(filepath)