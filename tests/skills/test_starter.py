from epsilon.skills import starter, store


def test_starter_prompt_demands_full_skill_md_and_one_block():
    text = starter.STARTER_PROMPT

    assert "---" in text and "name:" in text and "description:" in text
    assert "UN solo bloque de código" in text
    assert "No guardes nada" in text and "Guardar como skill" in text
    assert len(text) < 1500


def test_the_example_in_the_prompt_is_a_valid_skill_shape():
    block = starter.STARTER_PROMPT.split("```markdown\n", 1)[1].split("\n```", 1)[0]

    name, description, body = store.parse(block)

    assert name and description and body
