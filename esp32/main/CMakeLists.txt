if(CONFIG_POKE_TRANSPORT_UART)
    idf_component_register(SRCS "poke_edge_uart.c"
                           INCLUDE_DIRS ".")
else()
    idf_component_register(SRCS "poke_edge.c"
                           INCLUDE_DIRS ".")
endif()
