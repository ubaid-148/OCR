"""Notebook upload form; OCR starts only after the user presses Process PDFs."""
from html import escape
from pathlib import Path


def upload_values(value):
    entries = value.values() if isinstance(value, dict) else value
    return {entry['metadata']['name'] if 'metadata' in entry else entry['name']:
            bytes(entry['content']) for entry in entries}


def show_download(path, label='Download JSON'):
    import ipywidgets as widgets
    from IPython.display import display
    from google.colab import files
    if label == 'Download invoice JSON':
        import json
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        status = escape(str(data.get('status', 'Result ready')).replace('_', ' '))
        number = escape(str(data.get('invoice_number') or 'Not established'))
        display(widgets.HTML('<div style="padding:14px;border:1px solid #dbe3ee;border-radius:10px">'
            '<b>'+escape(Path(path).name)+'</b><p>Invoice: '+number+' · '+status+'</p>'
            '<details><summary>View JSON</summary><pre style="max-height:360px;overflow:auto;white-space:pre-wrap">'
            +escape(json.dumps(data,ensure_ascii=False,indent=2))+'</pre></details></div>'))
    button = widgets.Button(description=label, icon='download', layout=widgets.Layout(width='230px'))
    button.on_click(lambda _: files.download(str(path)))
    display(widgets.HTML('<b>'+escape(Path(path).name)+'</b>'), button)


def show_upload_form(process, mode='auto', language='eng+ara', diagnostics=False):
    import ipywidgets as widgets
    from IPython.display import display
    from google.colab import output
    output.enable_custom_widget_manager()
    picker = widgets.FileUpload(accept='.pdf', multiple=True, description='Choose PDFs',
                                layout=widgets.Layout(width='200px'))
    selected = widgets.HTML('<span style="color:#64748b">No PDFs selected yet.</span>')
    mode_widget = widgets.Dropdown(options=[('Accuracy (OCR + vision)', 'auto'), ('Fast (OCR only)', 'fast')],
                                  value=mode, description='Mode:')
    language_widget = widgets.Dropdown(options=[('English + Arabic','eng+ara'),('English','eng'),
        ('Arabic','ara'),('English + Urdu','eng+urd')],value=language,description='Language:')
    diagnostic_widget = widgets.Checkbox(value=diagnostics,description='Include diagnostic download')
    start = widgets.Button(description='Process PDFs', icon='play', button_style='primary', disabled=True,
                           layout=widgets.Layout(width='200px',height='42px'))
    status = widgets.HTML('Select one or more invoice PDFs to begin.')
    progress = widgets.IntProgress(value=0,max=1,layout=widgets.Layout(width='100%'))
    results = widgets.Output()
    logs = widgets.Output()
    log_panel = widgets.Accordion(children=[logs], selected_index=None)
    log_panel.set_title(0,'Processing log')

    def changed(change):
        values = upload_values(picker.value)
        selected.value = '<br>'.join(escape(name)+' <span style="color:#64748b">('+f'{len(data)/1024:.0f} KB'+')</span>'
                                    for name,data in values.items()) or 'No PDFs selected yet.'
        start.disabled = not bool(values)
    picker.observe(changed,names='value')

    def run(_):
        values = upload_values(picker.value)
        controls = [picker,start,mode_widget,language_widget,diagnostic_widget]
        for control in controls: control.disabled=True
        results.clear_output(); logs.clear_output()
        progress.max=max(1,len(values)); progress.value=0; progress.bar_style='info'
        status.value='<b>Processing…</b> Keep this runtime connected. Results will appear below.'
        def update(index,total,name):
            progress.value=index-1
            status.value=f'<b>Processing {index}/{total}</b> · {escape(name)}'
        try:
            with logs:
                process(values,mode_widget.value,language_widget.value,diagnostic_widget.value,update,results)
            progress.value=len(values); progress.bar_style='success'
            status.value='<b>Processing finished.</b> Check each result and any errors in the log.'
        except Exception as error:
            progress.bar_style='danger'
            status.value='<b>Processing failed:</b> '+escape(str(error))
        finally:
            for control in controls: control.disabled=False
            start.disabled=not bool(upload_values(picker.value))
    start.on_click(run)
    panel=widgets.VBox([
        widgets.HTML('<div style="padding:8px 0"><span style="color:#2563eb;font-weight:700">INVOICE OCR</span>'
                     '<h2 style="margin:8px 0">Turn your PDFs into invoice JSON</h2>'
                     '<p style="color:#64748b">Choose files, review your settings, then start extraction. '
                     'Each PDF gets its own result.</p></div>'),
        picker,selected,mode_widget,language_widget,diagnostic_widget,start,status,progress,
        widgets.HTML('<h3>Results</h3>'),results,log_panel],
        layout=widgets.Layout(border='1px solid #dbe3ee',padding='24px',max_width='820px',width='100%'))
    display(panel)
    return panel
