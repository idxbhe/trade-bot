from textual.app import App
from textual.reactive import reactive
import pandas as pd

class ReactiveApp(App):
    data = reactive(None, always_update=True)
    
    def on_mount(self):
        df = pd.DataFrame({"A": [1, 2]})
        self.data = df
        df2 = pd.DataFrame({"A": [1, 2]})
        self.data = df2
        print("SUCCESS")
        self.exit()

if __name__ == "__main__":
    ReactiveApp().run()
