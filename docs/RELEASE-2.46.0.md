# Zapret GUI 2.46.0

- Обновлены встроенные стратегии до [Flowseal zapret-discord-youtube 1.10.4](https://github.com/Flowseal/zapret-discord-youtube/releases/tag/1.10.4). Все 22 пресета собраны из BAT этого выпуска. В комплект вошли семь новых файлов TLS и QUIC и обновлённый список исключений.
- Встроенный Telegram-прокси обновлён до [tg-ws-proxy 1.12.0](https://github.com/Flowseal/tg-ws-proxy/releases/tag/v1.12.0). При отключении клиента прокси отменяет незавершённое подключение. Если резервный узел отвечает HTTP 429, приложение учитывает Retry-After и возвращает узел в пул максимум через пять минут.
