# GymGarmin Sync (Android)

App pequeno que lê o **Health Connect** (Xiaomi Mi Fitness, Samsung Health, Amazfit/Zepp, Google Fit, Fitbit…)
e envia treinos, sono, FC de repouso, VFC, passos, VO2máx e peso para `/api/ingest` com o código pessoal
da conta (GymGarmin → Perfil → Outros relógios). Depois do primeiro envio, sincroniza a cada ~6 h (WorkManager).

## Gerar o APK

```bash
export ANDROID_HOME=/caminho/do/android-sdk   # platforms;android-36 e build-tools;36.0.0
./gradlew assembleRelease
cp app/build/outputs/apk/release/app-release.apk ../gymgarmin/static/gymgarmin-sync.apk
```

`gymgarmin-sync.keystore` assina todas as versões com a mesma chave (assim uma atualização instala por
cima da anterior). É uma chave só deste app de família, fora da Play Store; não use para outra coisa.
