FROM espressif/idf:v5.4

WORKDIR /poke
COPY main/ ./main/
COPY CMakeLists.txt ./

# Set UART mode + disable memory protection for code injection
RUN bash -c "source /opt/esp/idf/export.sh && \
    idf.py set-target esp32c3 && \
    idf.py build && \
    sed -i 's/CONFIG_ESP_SYSTEM_MEMPROT_FEATURE=y/CONFIG_ESP_SYSTEM_MEMPROT_FEATURE=n/' sdkconfig && \
    sed -i 's/CONFIG_ESP_SYSTEM_MEMPROT_FEATURE_LOCK=y/CONFIG_ESP_SYSTEM_MEMPROT_FEATURE_LOCK=n/' sdkconfig && \
    sed -i 's/CONFIG_POKE_TRANSPORT_WIFI=y/# CONFIG_POKE_TRANSPORT_WIFI is not set/' sdkconfig && \
    echo 'CONFIG_POKE_TRANSPORT_UART=y' >> sdkconfig && \
    idf.py build"
