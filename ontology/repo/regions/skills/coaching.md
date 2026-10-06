---
id: coaching
name: "Voice coaching"
kind: procedure
one_liner: "A person watches and talks; comments score attempts, name parts, stop, or declare done"
injected_by: tools/ontology.py
parent: skills
---
# Voice coaching (sim/body/coach.py)
The person's words are the reward: "쥐었어" +2, "말았어" +1, "움직였어" +0.5, "풀렸어/반대야" −1.5.
"멈춰" freezes at once without the model. "그거야" saves the skill, its tuned numbers and its POKE
image to the device's SD (edge store). A comment that names a part ("끝마디도 더 굽혀") pushes only
the params named after that part. Fast loop: explore around praised params. Slow loop: when praise
stalls, the model rewrites the structure from the transcript. Speech lags what it describes by
about 0.8 s; utterances are matched to the attempt a moment earlier. On the twin a stand-in
watcher with ground truth plays the person.
