# Компоненты

Собственный код нативного клиента и браузерного моста находится в `engines/native` и `engines/bridge`. Готовый релиз включает следующие сторонние компоненты.

- sing-box 1.12.0: официальный [исходный код](https://github.com/SagerNet/sing-box/tree/v1.12.0), GPL-3.0-or-later; проверенные SHA-256 архивов находятся в scripts/fetch_vpn.py. Подписки преобразуются в собственную минимальную конфигурацию; настройки маршрутизации провайдера не исполняются.
- Xray 26.10.21: официальный [исходный код](https://github.com/XTLS/Xray-core/tree/v26.10.21), MPL-2.0; SHA-256 официальных ZIP закреплены в scripts/fetch_xray.py. Лицензия в assets/vpn/licenses/xray-LICENSE. Используется для XHTTP, включая его собственный TUN.
- PyYAML 6.0.3: MIT, лицензия в alisa_cli/_vendor/PyYAML-LICENSE, происхождение и SHA-256 в docs/pyyaml-source.json.
- Questionary 2.1.1, prompt_toolkit 3.0.53 и wcwidth 0.9.4: MIT/BSD-лицензии находятся в соответствующих `.dist-info/licenses` или `.dist-info` каталогах внутри alisa_cli/_vendor. Происхождение и SHA-256 официальных wheel-файлов закреплены в docs/terminal-ui-sources.json. Эти библиотеки работают только в CLI на компьютере, не на колонке.
- WebRTC NetEq, Abseil, libdatachannel, libjuice, usrsctp, libsrtp, plog, nlohmann/json, curl, OpenSSL, Opus: тексты лицензий в assets/native/licenses. Точные Git-коммиты, в том числе submodules, закреплены в [docs/native-dependencies.json](docs/native-dependencies.json). NetEq использует набор декодера из `linux_x86_64.cmake` закреплённого upstream; скрипт `engines/native/scripts/prepare_neteq.py` вычисляет его зависимости. Sonic нужен только для альтернативной сборки с `ALISA_USE_NETEQ=OFF`; в штатном NetEq-клиенте не используется.
- Библиотека Opus браузерного моста — пакет Debian `libopus0 1.3.1-3 armhf`; URL и SHA-256 закреплены в `engines/bridge/scripts/fetch_opus.py`, лицензия — `engines/bridge/build/opus-copyright`.
- Chromium, PipeWire/PulseAudio, Xvfb и OpenSSH устанавливаются из пакетов дистрибутива, в архив программы не входят.

В [релизе](https://github.com/rison1337/Alisa-ChatGPT-voice-assistant/releases/tag/v0.2.0) рядом с установочным архивом доступен `alisa-voice-sources.tar.gz` и SHA-256: закреплённые исходники нативных зависимостей, исходники sing-box/Xray и их Go-зависимости. Инструкции сборки — [docs/development.md](docs/development.md). Официальные VPN-бинарники не модифицировались; скрипты загрузки проверяют SHA-256 upstream-архивов.

Проприетарная прошивка Яндекса, сертификаты, ключи, браузерные профили и данные аккаунтов в установочный архив не входят.
