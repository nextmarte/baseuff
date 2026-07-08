from uff_server.pii import mask_cpf


def test_mask_cpf_formatted():
    assert mask_cpf("Fiscal CPF: 032.123.456-78, matrícula 1063273") == (
        "Fiscal CPF: ***.***.***-**, matrícula 1063273"
    )


def test_mask_cpf_multiple():
    assert mask_cpf("A 300.111.222-33 e B 022.444.555-66").count("***.***.***-**") == 2


def test_does_not_touch_process_number():
    # nº de processo (23069.xxxxxx/aa-dd) NÃO é CPF e deve permanecer
    txt = "Processo 23069.011900/09-30 do servidor"
    assert mask_cpf(txt) == txt


def test_masks_bare_11_digit_cpf_only_with_label():
    # 11 dígitos crus são ambíguos (SIAPE/processo); só mascara quando rotulado como CPF
    assert mask_cpf("CPF 03212345678 fim") == "CPF ***.***.***-** fim"
    assert mask_cpf("SIAPE 1063273 nº 10630795") != "SIAPE ***.***.***-** nº 10630795"


def test_empty_and_none_safe():
    assert mask_cpf("") == ""
    assert mask_cpf(None) is None
