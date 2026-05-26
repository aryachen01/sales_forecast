Set-Location "$PSScriptRoot\.."
python main.py `
  --scenario bq_local_local `
  --config config/profiles/item_day/config_v001.yaml
