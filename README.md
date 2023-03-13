This plugin tries to parse metadata and cover information from kitapyurdu.com, a Turkish online bookstore.

[Github repository](https://github.com/anezih/calibre-kitapyurdu)

**Main Features:**
- Plugin is capable of retrieving: the title, author, rating, tags, ISBN, publication date, publisher, language and comments (short description of the book).
- Plugin sets **kitapyurdu:** identifier for easy access from the details tab. It will show up as **Kitapyurdu**. Additionaly, it will set the **kitapyurdu_kapak** identifer in order to cache cover url. The latter won't be shown in the details tab. **Note**: If you know the book you try to download its metadata is on the site but the plugin can't fetch it, then you can set its **kitapyurdu** identifier manually. Copy the numeric value before the **.html** in the address bar and paste it in the identifiers field with the format **kitapyurdu:123816**, for example.

**Configuration:**
- You can configure the maximum number of search results to be parsed in plugin settings. Choices are: 20, 25, 50. Default: 20.
- You can choose to append extra metadata (Editor(s), Translator(s), Original name, page number) to the end of the description. Default: Unchecked.

<details>
<summary><b>Changelog:</b></summary>
<b>Ver. 1.1.0</b>
<li>Set minimum Calibre version.</li>
<li>Added an option to append extra metadata to the end of the description.</li>
<li>Try to fetch metadata with <b>kitapyurdu</b> identifier first.</li>
<b>Ver. 1.0.0</b>
<li>Initial release</li>
</details>
