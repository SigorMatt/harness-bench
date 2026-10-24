**Bug report: values that contain `=` are cut short**

Our `.env` file has

```
DATABASE_URL=postgres://app:secret@db/app?sslmode=require
```

but `envfile.parse()` gives `postgres://app:secret@db/app?sslmode` for it.
Base64 tokens lose their padding: `TOKEN=c2VjcmV0==` comes back as
`c2VjcmV0`. And a quoted value such as `GREETING="a=b"` makes `parse()` raise
`EnvFileError`, although the line is valid.

Please fix this. Everything else about how `.env` files are read and written
should stay as it is.
