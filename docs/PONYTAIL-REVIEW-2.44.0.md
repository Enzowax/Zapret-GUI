tgproxy/_aes.py:L12: native: При сохранении Windows-only назначения встроенного пакета убрать резервную реализацию AES через Linux libcrypto (127 строк). Заменить импортом Cipher, algorithms, modes из обязательной cryptography и __all__; это увеличивает расхождение с upstream и исключает его сценарий запуска на роутерах.

zapret_core.py:L1149: shrink: Убрать ручное закрытие сокета в _port_in_use. Использовать with socket.socket() as s внутри try, сохранив обе ветви исключений; экономия 5 строк.

utils/test zapret.ps1:L49: native: Удалить New-OrderedDict и Add-OrSet (5 строк). Заменить вызовы на [ordered]@{} и $dict[$key] = $value; присваивание OrderedDictionary добавляет отсутствующий ключ и обновляет существующий.

Применено в финальной версии: неиспользуемый _trigger_auto_research удалён (10 строк).

net: -137 lines, -0 deps possible.
