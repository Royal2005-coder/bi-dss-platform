"""Export DAX measures, data dictionary and relationships from the solution PBIX."""
import sys
from pbixray import PBIXRay

pbix, out = sys.argv[1], sys.argv[2]
m = PBIXRay(pbix)
with open(f"{out}/SkyOps_DAX_Measures.dax", "w", encoding="utf-8") as f:
    for _, r in m.dax_measures.iterrows():
        f.write(f"// {r.TableName}[{r.Name}]\n{r.Name} =\n{r.Expression.strip()}\n\n")
schema = m.schema
user = schema[~schema.TableName.str.contains("LocalDateTable|DateTableTemplate")]
user.to_csv(f"{out}/SkyOps_Data_Dictionary.csv", index=False, encoding="utf-8-sig")
m.relationships.to_csv(f"{out}/SkyOps_Model_Relationships.csv", index=False, encoding="utf-8-sig")
print(len(m.dax_measures), "measures;", len(user), "columns;", len(m.relationships), "relationships")
