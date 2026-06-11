FROM espressif/idf:v5.4

WORKDIR /poke
COPY main/ ./main/
COPY CMakeLists.txt ./

RUN bash -c "source /opt/esp/idf/export.sh && idf.py set-target esp32c3 && idf.py build"
