"""Synthetic JPEG regression measurement; offscreen only, no user images."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter, sleep
os.environ['QT_QPA_PLATFORM']='offscreen'
parser=argparse.ArgumentParser()
parser.add_argument('--repo',type=Path,required=True)
parser.add_argument('--zip',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
sys.path.insert(0,str(args.repo))
from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtGui import QPixmap,QWheelEvent
from PySide6.QtWidgets import QApplication
from app.config_manager import ConfigManager
from app.viewer_window import ViewerWindow
app=QApplication([])
rows=[]
for repeat in range(2):
  for pattern in ('forward','reverse','bounce','rapid'):
    with tempfile.TemporaryDirectory(prefix='nivis-jpeg-check-') as temp:
      config=ConfigManager(Path(temp)/'config.json');config.load()
      config.apply({'view_mode':'single','single_first_page':False,'show_page_list':False,'viewer_memory_mode':'4096'})
      window=ViewerWindow(config_manager=config)
      window.resize(1600,1000);window.ensurePolished();window.layout().activate()
      paint=[True]
      window.presentationCommitted.connect(lambda _: paint.__setitem__(0,True))
      def pump():
        app.processEvents()
        if paint[0]:
          paint[0]=False
          pm=QPixmap(window.viewer.size());window.viewer.render(pm)
        sleep(.0005)
      def until(predicate):
        deadline=perf_counter()+45
        while not predicate():
          if perf_counter()>deadline: raise TimeoutError(pattern)
          pump()
      try:
        opened=perf_counter();assert window.open_path(args.zip)
        until(lambda:window.presentation_state.displayed_page==0)
        first=(perf_counter()-opened)*1000
        runtime=window.book_session.viewer_runtime
        for mode in ('cold','warm'):
          if mode=='warm':
            until(lambda:not runtime.has_unfinished_tasks())
          timings=[];accepted=0;packets=0;peak=0
          def wheel(direction):
            before=window.model.focused_index
            event=QWheelEvent(QPointF(10,10),QPointF(10,10),QPoint(),QPoint(0,-120*direction),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
            event.setTimestamp(int(perf_counter()*1000));app.sendEvent(window.viewer,event)
            return before!=window.model.focused_index
          start=perf_counter()
          sequence=([7] if pattern in ('forward','rapid') else [7,0] if pattern=='reverse' else [7,0,7,0])
          for target in sequence:
            while window.presentation_state.displayed_page!=target:
              direction=1 if window.model.focused_index<target else -1
              if window.model.focused_index==target:
                until(lambda:window.presentation_state.displayed_page==target);break
              ts=perf_counter();accepted+=wheel(direction);packets+=1
              if pattern=='rapid':
                deadline=ts+.020
                while perf_counter()<deadline: pump()
              else:
                expected=window.model.focused_index
                until(lambda:window.presentation_state.displayed_page==expected)
              timings.append((perf_counter()-ts)*1000)
              peak=max(peak,runtime.cache_bytes+sum(runtime._inflight_reservations.values()))
              if perf_counter()-start>60:raise TimeoutError(pattern)
          rows.append({'repeat':repeat,'pattern':pattern,'mode':mode,'first_ms':round(first,2),
                       'elapsed_ms':round((perf_counter()-start)*1000,2),'packets':packets,'accepted':accepted,
                       'input_wait_ms': [round(t,2) for t in timings], 'peak_accounted_bytes':peak,'metrics':asdict(runtime.metrics)})
          # Return to page zero before warming the identical input sequence.
          while window.presentation_state.displayed_page!=0:
            wheel(-1);until(lambda:window.presentation_state.displayed_page==window.model.focused_index)
          until(lambda:not runtime.has_unfinished_tasks())
      finally:
        window.prepare_shutdown(wait_msecs=10000);window.close();app.processEvents()
args.output.write_text(json.dumps(rows,indent=2),encoding='utf-8')
print(json.dumps([{k:v for k,v in row.items() if k not in ('metrics','input_wait_ms')} for row in rows]))
